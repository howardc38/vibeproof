"""Kernel tests.  Run with: python3 -m pytest tests/ -q   (or unittest)

These started as shell one-liners proving the two mechanisms the design leans
on hardest.  They live here so `test_command` has something real to run when
this repo becomes its own first adopter.
"""

import argparse
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.analysis import subject_files as _subject_files  # noqa: E402
from kernel import (cli, composition, config, derive, engagement, hashing,  # noqa: E402
                    ledger, lifecycle, register, review, risk, runner, state)

REPO = Path(__file__).resolve().parent.parent


def _git_repo(tmp: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, check=True)
    return tmp


class _Stdin:
    """A stdin that answers the one question `risk.accept` asks it.

    `sys.stdin.isatty()` decides which of three values a signature records, and
    a test that reads the ambient answer is asserting about how the suite was
    started rather than about the code. Measured 2026-08-27, before this
    existed: `python3 -m unittest tests.test_kernel.Signing` passed at a pipe
    and failed six of its twelve tests under `pty.spawn`, because the worker
    route recorded `person` where the assertion said `agent`.

    Nothing else in these tests reads stdin, so replacing the object is enough
    and no `TextIOWrapper` behaviour has to be imitated.
    """

    def __init__(self, tty: bool):
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


def _attempt(conn, code, stdout=""):
    return ledger.append_attempt(
        conn, claim_id="c1", subject_digest={"file:x.py": "d"}, checker_sha="s",
        config_sha="cfg", head_commit="abc", worktree=".", argv=["x"],
        exit_code=code, stdout=stdout, stderr="", started_at="2026",
        ended_at="2026", duration_ms=1,
    )


class Ledger(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t1", request="r", scope_globs=["a/**"],
                      base_commit="abc", created_at="2026")
        ledger.insert(self.conn, "claim", id="c1", task_id="t1", kind="test",
                      question="q", subject_refs=[{"kind": "file", "path": "x.py"}],
                      checker="test", origin="derive", created_at="2026")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_chain_is_intact_when_written_through_the_kernel(self):
        for code in (1, 1, 0):
            _attempt(self.conn, code)
        ok, problems = ledger.audit_chain(self.conn)
        self.assertTrue(ok, problems)

    def test_update_is_refused(self):
        _attempt(self.conn, 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE attempt SET exit_code = 0 WHERE id = 1")

    def test_delete_is_refused(self):
        _attempt(self.conn, 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("DELETE FROM attempt WHERE id = 1")

    FORGE = (
        "INSERT INTO attempt (claim_id,subject_digest,checker_sha,config_sha,"
        "head_commit,worktree,argv,exit_code,stdout,stderr,started_at,ended_at,"
        "duration_ms,prev_hash,row_hash) VALUES "
        "('c1','{}','s','cfg','abc','.','[]',0,'FORGED','','2026','2026',1,'x','y')"
    )

    def test_an_outside_connection_cannot_insert_at_all(self):
        """The one-liner the design used to cite as unpreventable.

        For a long time this test asserted the opposite -- that a forged row
        lands and audit catches it afterwards -- because the design's §2.1 said
        prevention was impossible without a daemon or a separate uid. That is
        true of a boundary. It was the wrong conclusion about this, and the
        predecessor had already shipped the counter-example: an INSERT trigger
        calling a function only the writing module registers.
        """
        _attempt(self.conn, 1)
        path = ledger.ledger_path(self.tmp)
        self.conn.close()

        raw = sqlite3.connect(path)
        with self.assertRaises(sqlite3.OperationalError) as caught:
            raw.execute(self.FORGE)
        raw.close()
        self.assertIn("v4_kernel_can_write", str(caught.exception))
        self.conn = ledger.connect(self.tmp)

    def test_dropping_the_gate_works_and_the_chain_still_catches_it(self):
        """It is friction, not a boundary, and the second half is what holds.

        Anyone who can write the file can drop the trigger. The gate is worth
        having anyway: it moves the cost from one shell command to two, and the
        row that gets in still fails the chain.
        """
        _attempt(self.conn, 1)
        path = ledger.ledger_path(self.tmp)
        self.conn.close()

        raw = sqlite3.connect(path)
        raw.execute("DROP TRIGGER gate_insert_attempt")
        raw.execute(self.FORGE)
        raw.commit()
        raw.close()

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        ok, problems = ledger.audit_chain(conn)
        conn.close()
        self.conn = ledger.connect(self.tmp)          # for tearDown
        self.assertFalse(ok)
        self.assertTrue(any("prev_hash" in p for p in problems), problems)

    def test_connecting_restores_a_dropped_gate(self):
        """So a forged row sits in a database whose schema says it could not be."""
        path = ledger.ledger_path(self.tmp)
        self.conn.execute("DROP TRIGGER gate_insert_attempt")
        self.conn.commit()
        self.conn.close()

        self.conn = ledger.connect(self.tmp)
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        self.assertIn("gate_insert_attempt", names)

    def test_attempts_cannot_go_through_the_generic_insert(self):
        with self.assertRaises(RuntimeError):
            ledger.insert(self.conn, "attempt", claim_id="c1")


class Identity(unittest.TestCase):
    def test_line_number_is_not_part_of_identity(self):
        """The rev-1 bug: shifting a symbol down the file minted a new claim."""
        a = hashing.claim_id("t1", "fail-closed", "x.py", "publish", "swallow")
        b = hashing.claim_id("t1", "fail-closed", "x.py", "publish", "swallow")
        self.assertEqual(a, b)

    def test_same_site_in_two_tasks_is_two_claims(self):
        a = hashing.claim_id("t1", "fail-closed", "x.py", "publish", "swallow")
        b = hashing.claim_id("t2", "fail-closed", "x.py", "publish", "swallow")
        self.assertNotEqual(a, b)

    def test_variant_separates_claims_at_one_site(self):
        a = hashing.claim_id("t1", "external-write", "x.py", "send", "readback")
        b = hashing.claim_id("t1", "external-write", "x.py", "send", "replay")
        self.assertNotEqual(a, b)

    def test_a_missing_file_hashes_to_absent_rather_than_raising(self):
        self.assertEqual(hashing.file_digest(Path("/nope/nope.py")), hashing.ABSENT)


class RegistrationGate(unittest.TestCase):
    """The gate exists to stop a checker that detects nothing.  Prove it does."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.fx = self.tmp / "fx"
        for colour, body in (("red", "except Exception as e:\n        log(e)\n"),
                             ("green", "except Exception:\n        raise\n")):
            d = self.fx / colour
            d.mkdir(parents=True)
            for i in range(5):
                (d / f"case{i}.py").write_text(
                    f"def f{i}():\n    try:\n        requests.post(URL)\n    {body}"
                )
        # Three evasions, and each has to still be caught. The gate now demands
        # them, which is why this synthetic set needs them too: a rule that only
        # applies to other people's fixtures is not a rule.
        b = self.fx / "bypass"
        b.mkdir(parents=True)
        (b / "raise_after_return.py").write_text(
            "def f():\n    try:\n        requests.post(URL)\n"
            "    except Exception:\n        return None\n"
            "        raise RuntimeError('unreachable')\n")
        (b / "raise_in_a_nested_def.py").write_text(
            "def f():\n    try:\n        requests.post(URL)\n"
            "    except Exception:\n        def _later():\n"
            "            raise RuntimeError('nope')\n        return None\n")
        (b / "raise_only_as_a_word.py").write_text(
            "def f():\n    try:\n        requests.post(URL)\n"
            "    except Exception:\n        log('would raise')\n        return None\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, checker_src):
        checker = self.tmp / "checker.py"
        checker.write_text(checker_src)
        return register.verify_checker(
            repo_root=self.tmp, checker_path=checker,
            fixtures_dir=self.fx, kind="selftest",
        )

    #: The question is whether control can leave the handler without raising,
    #: not whether a `raise` token appears anywhere inside it. The first version
    #: asked the second, and the bypass cases the gate now demands walked
    #: straight through it: a raise after an unconditional return, and a raise
    #: inside a nested function nobody calls. The gate made the checker correct.
    REAL = '''
import argparse, ast, json, sys
from pathlib import Path
p = argparse.ArgumentParser(); p.add_argument("--subject"); p.add_argument("--facts"); p.add_argument("--out")
a = p.parse_args()
s = json.loads(Path(a.subject).read_text()); root = Path(s["repo_root"]); bad = []


def escapes(handler):
    """True when control can leave this handler without raising."""
    for stmt in handler.body:
        # A nested definition is not this handler's control flow.
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.Raise):
            return False
        if isinstance(stmt, ast.Return):
            return True
    return True


for ref in s["subject_refs"]:
    t = ast.parse((root / ref["path"]).read_text())
    for n in ast.walk(t):
        if isinstance(n, ast.Try):
            for h in n.handlers:
                if escapes(h):
                    bad.append(ref["path"])
for b in bad: print(b)
sys.exit(1 if bad else 0)
'''

    def test_a_real_checker_is_registrable(self):
        ok, report = self._run(self.REAL)
        self.assertTrue(ok, report["failures"])
        self.assertEqual(report["passed"], report["total"])

    def test_a_mistyped_fixtures_path_does_not_unregister_anything(self):
        """"Your fixtures failed" and "I found no fixtures" went down one path.

        A refusal removes the previous entry on purpose -- measured, 21 of 23
        kinds were refused on a real adoption and every one kept running on an
        entry an earlier install had left. But a directory that does not exist
        is not a fixture run: the gate needs 5 red, 5 green and 3 bypass cases
        to accept, so zero cases means the command named nothing. Typed
        `tests/fixtures/dependency` for a directory called `dependency_audit`
        and a working checker came back with `path: null`.
        """
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, capture_output=True)
        checker = self.tmp / "checker.py"
        checker.write_text(self.REAL)
        reg = self.tmp / "checkers.json"
        reg.write_text(json.dumps(
            {"selftest": {"path": "checkers/x.py", "sha256": "abc",
                          "reads": ["**/*.py"]}}))

        ok, report = register.register(
            repo_root=self.tmp, checker_id="selftest", checker_path=checker,
            fixtures_dir=self.tmp / "no_such_dir", kinds=["selftest"],
            checkers_json=reg, conn=ledger.connect(self.tmp), timeout_sec=60)
        self.assertFalse(ok)
        self.assertTrue(report.get("found_no_fixtures"), report)
        self.assertEqual(report["total"], 0)
        after = json.loads(reg.read_text())
        self.assertIn("selftest", after)                  # left alone
        self.assertEqual(after["selftest"]["reads"], ["**/*.py"])

    def test_a_real_fixture_failure_still_unregisters(self):
        """The other half, so the fix above cannot be read as "refusals are
        harmless now"."""
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, capture_output=True)
        checker = self.tmp / "checker.py"
        checker.write_text("import sys\nsys.exit(0)\n")     # detects nothing
        reg = self.tmp / "checkers.json"
        reg.write_text(json.dumps({"selftest": {"path": "checkers/x.py"}}))

        ok, report = register.register(
            repo_root=self.tmp, checker_id="selftest", checker_path=checker,
            fixtures_dir=self.fx, kinds=["selftest"],
            checkers_json=reg, conn=ledger.connect(self.tmp), timeout_sec=60)
        self.assertFalse(ok)
        self.assertFalse(report.get("found_no_fixtures"))
        self.assertNotIn("selftest", json.loads(reg.read_text()))

    def test_a_checker_that_always_passes_is_refused(self):
        ok, report = self._run("import sys\nsys.exit(0)\n")
        self.assertFalse(ok)
        self.assertEqual(sum(1 for f in report["failures"] if f.startswith("red/")), 5)

    def test_a_checker_that_always_fails_is_refused(self):
        ok, report = self._run("import sys\nsys.exit(1)\n")
        self.assertFalse(ok)
        self.assertEqual(sum(1 for f in report["failures"] if f.startswith("green/")), 5)

    def test_a_nondeterministic_checker_is_refused(self):
        ok, report = self._run("import random, sys\nsys.exit(random.choice([0, 1]))\n")
        self.assertFalse(ok)

    def test_too_few_fixtures_is_refused(self):
        for f in (self.fx / "red").iterdir():
            f.unlink()
            break
        ok, report = self._run(self.REAL)
        self.assertFalse(ok)
        self.assertTrue(any("need at least" in f for f in report["failures"]))


class Runner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / "x.py").write_text("value = 1\n")
        self.checker = self.tmp / "c.py"
        self.checker.write_text("import sys\nsys.exit(0)\n")
        self.refs = [{"kind": "file", "path": "x.py"}]
        self.payload = {"repo_root": str(self.tmp), "subject_refs": self.refs}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, **kw):
        return runner.run_checker(
            repo_root=self.tmp, checker_path=self.checker,
            subject_payload=self.payload, subject_refs=self.refs, **kw,
        )

    def test_a_swapped_checker_is_refused_before_it_runs(self):
        """Editing the checker must not be a way to turn a FAIL into a PASS."""
        res = self._run(registered_sha="0" * 64)
        self.assertEqual(res.exit_code, runner.CHECKER_TAMPERED)
        self.assertIn("not the registered one", res.stderr)

    def test_matching_sha_is_allowed(self):
        res = self._run(registered_sha=hashing.file_sha(self.checker))
        self.assertEqual(res.exit_code, runner.PASS)

    def test_timeout_is_not_a_failure(self):
        self.checker.write_text("import time\ntime.sleep(10)\n")
        res = self._run(registered_sha=None, timeout_sec=1)
        self.assertEqual(res.exit_code, runner.TIMEOUT)

    def test_a_subject_edited_mid_run_is_caught(self):
        self.checker.write_text(
            "import json, sys\nfrom pathlib import Path\n"
            "import argparse\n"
            "p = argparse.ArgumentParser(); p.add_argument('--subject')\n"
            "p.add_argument('--facts'); p.add_argument('--out'); a = p.parse_args()\n"
            "s = json.loads(Path(a.subject).read_text())\n"
            "Path(s['repo_root'], 'x.py').write_text('value = 2\\n')\n"
            "sys.exit(0)\n"
        )
        res = self._run(registered_sha=None)
        self.assertEqual(res.exit_code, runner.SUBJECT_MOVED)

    def test_a_crash_is_not_read_as_a_finding(self):
        """A red case wants exit 1 and a crash gives it one.

        `reported_nothing` exists because of that: 21 checkers were refused by
        fixtures they pass, and their red cases "passed" by dying. It had no
        test at all -- changing its body to `return False` left all 688 green,
        because every checker in this suite exits cleanly and nothing here had
        ever run one that raises.
        """
        self.checker.write_text(
            "import argparse\n"
            "p = argparse.ArgumentParser(); p.add_argument('--subject')\n"
            "p.add_argument('--facts'); p.add_argument('--out'); p.parse_args()\n"
            "raise RuntimeError('the checker itself is broken')\n")
        res = self._run(registered_sha=None)
        self.assertEqual(
            res.exit_code, runner.ERROR,
            "a checker that died with a traceback and nothing on stdout was "
            "recorded as a FAIL, which reads as a finding about the repo")

    def test_a_real_finding_on_stderr_is_still_a_finding(self):
        """The other half: exit 1 with something said is a verdict, not a crash."""
        self.checker.write_text(
            "import argparse, sys\n"
            "p = argparse.ArgumentParser(); p.add_argument('--subject')\n"
            "p.add_argument('--facts'); p.add_argument('--out'); p.parse_args()\n"
            "print('FAIL: x.py does the thing this checker refuses')\n"
            "sys.exit(1)\n")
        res = self._run(registered_sha=None)
        self.assertEqual(res.exit_code, runner.FAIL)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class RulerFreeze(unittest.TestCase):
    """A frozen ruler that nothing enforces is just a comment."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        self.acc = self.tmp / "acceptance.json"
        self.acc.write_text('{"criteria": {"red_must_exit": 1}}')
        self.conn = ledger.connect(self.tmp)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_round_means_no_constraint(self):
        self.assertIsNone(register.assert_ruler_unmoved(self.conn, self.acc))

    def test_unchanged_criteria_pass(self):
        register.open_round(self.conn, self.acc, "r1")
        self.assertIsNotNone(register.assert_ruler_unmoved(self.conn, self.acc))

    def test_editing_criteria_mid_round_is_refused(self):
        register.open_round(self.conn, self.acc, "r1")
        self.acc.write_text('{"criteria": {"red_must_exit": 0}}')
        with self.assertRaises(register.RulerMoved):
            register.assert_ruler_unmoved(self.conn, self.acc)

    def test_amending_between_rounds_is_allowed(self):
        register.open_round(self.conn, self.acc, "r1")
        register.close_round(self.conn, "r1")
        self.acc.write_text('{"criteria": {"red_must_exit": 0}}')
        self.assertIsNone(register.assert_ruler_unmoved(self.conn, self.acc))
        register.open_round(self.conn, self.acc, "r2")
        self.assertIsNotNone(register.assert_ruler_unmoved(self.conn, self.acc))

    def test_the_pinned_criteria_are_kept_not_just_their_hash(self):
        register.open_round(self.conn, self.acc, "r1")
        rnd = register.current_round(self.conn)
        self.assertEqual(rnd["acceptance"]["criteria"]["red_must_exit"], 1)


class UncommittedWorkIsNotInvisible(unittest.TestCase):
    """The attack this class exists for, in order:

        1. write a stub that passes
        2. `v4 check` -- test/lint/scope all green
        3. write the real code, do not commit
        4. `v4 ship` -- HEAD never moved, so the greens still stand

    Keying repo-scoped staleness on HEAD makes that work, and it leaves no
    trace: the ledger is clean and the chain verifies. Content is the key.
    """

    KINDS = {"test": {"staleness": "repo"}, "ext": {"staleness": "subject"}}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / "app.py").write_text("def notify():\n    return 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "stub"], cwd=self.tmp, check=True)
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t1", request="r", scope_globs=["*"],
                      base_commit="x", created_at="2026")
        ledger.insert(self.conn, "claim", id="ctest", task_id="t1", kind="test",
                      question="q", subject_refs=[], checker="test",
                      origin="derive", created_at="2026")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self):
        row = self.conn.execute("SELECT * FROM claim WHERE id='ctest'").fetchone()
        return state.claim_state(self.conn, self.tmp, row, kinds_cfg=self.KINDS,
                                 config_sha="cfg", checker_sha_of=lambda _: "sha")

    def _pass_now(self):
        ledger.append_attempt(
            self.conn, claim_id="ctest", subject_digest={}, checker_sha="sha",
            config_sha="cfg", head_commit=hashing.worktree_digest(self.tmp),
            worktree=str(self.tmp), argv=["pytest"], exit_code=0, stdout="1 passed",
            stderr="", started_at="2026", ended_at="2026", duration_ms=1)

    def test_an_uncommitted_edit_reopens_a_repo_scoped_claim(self):
        self._pass_now()
        self.assertEqual(self._state(), state.ANSWERED)

        (self.tmp / "app.py").write_text("def notify():\n    return send_everything()\n")
        self.assertEqual(
            self._state(), state.STALE,
            "an uncommitted rewrite left a repo-scoped claim answered; that is the "
            "whole attack -- pass a stub, then ship something else",
        )

    def test_an_untracked_file_also_reopens_it(self):
        self._pass_now()
        (self.tmp / "sneaky.py").write_text("import os\n")
        self.assertEqual(self._state(), state.STALE)

    def test_editing_the_facts_table_reopens_only_what_reads_it(self):
        """`RepoConfig.facts_sha` existed and nothing read it.

        So editing `.v4/facts.json` expired no answer anywhere -- an
        `external-write` PASS given under one set of outbound patterns stayed
        answered under another. Charging every kind would be the other error:
        22 of 29 declare the `--facts` flag and never read the value, and
        re-running a four-minute suite because an unrelated glob moved is how a
        staleness rule stops being obeyed.
        """
        ledger.insert(self.conn, "claim", id="cf", task_id="t1", kind="ext",
                      question="q", subject_refs=[], checker="ext",
                      origin="derive", created_at="2026")
        ledger.append_attempt(
            self.conn, claim_id="cf", subject_digest={}, checker_sha="sha",
            config_sha="cfg", head_commit=hashing.worktree_digest(self.tmp),
            worktree=str(self.tmp), argv=["x"], exit_code=0, stdout="", stderr="",
            started_at="2026", ended_at="2026", duration_ms=1, facts_sha="aaa")
        row = self.conn.execute("SELECT * FROM claim WHERE id='cf'").fetchone()

        def st(facts_of):
            return state.claim_state(self.conn, self.tmp, row, kinds_cfg=self.KINDS,
                                     config_sha="cfg", checker_sha_of=lambda _: "sha",
                                     facts_sha_of=facts_of)

        self.assertEqual(st(lambda _: "aaa"), state.ANSWERED)
        self.assertEqual(
            st(lambda _: "bbb"), state.STALE,
            "the table this checker scans against moved and its answer stood")
        self.assertEqual(
            st(lambda _: ""), state.STALE,
            "a kind that stopped reading the table is a different program too")
        self.assertEqual(
            st(None), state.ANSWERED,
            "a caller that does not pass the resolver must not invent a verdict")

    def test_a_kind_that_does_not_read_facts_is_not_charged(self):
        """The half that keeps the rule cheap enough to obey."""
        import sys as _sys
        from kernel import config as config_mod
        ledger.insert(self.conn, "claim", id="cn", task_id="t1", kind="ext",
                      question="q", subject_refs=[], checker="ext",
                      origin="derive", created_at="2026")
        ledger.append_attempt(
            self.conn, claim_id="cn", subject_digest={}, checker_sha="sha",
            config_sha="cfg", head_commit=hashing.worktree_digest(self.tmp),
            worktree=str(self.tmp), argv=["x"], exit_code=0, stdout="", stderr="",
            started_at="2026", ended_at="2026", duration_ms=1, facts_sha="")
        row = self.conn.execute("SELECT * FROM claim WHERE id='cn'").fetchone()
        # `facts_sha_for` returns "" for a checker that never reads the table,
        # whatever the table now hashes to.
        self.assertEqual(
            state.claim_state(self.conn, self.tmp, row, kinds_cfg=self.KINDS,
                              config_sha="cfg", checker_sha_of=lambda _: "sha",
                              facts_sha_of=lambda _: ""),
            state.ANSWERED)

    def test_a_changed_detector_reopens_what_it_raised(self):
        """The rule that raised a claim is part of what answered it.

        Every hand-built claim in this suite sets `detector=""`, and the claims
        `derive` builds with a real detector never get an attempt -- so
        `claim_state` returned OPEN before ever reaching this branch. Deleting
        the branch outright left all 688 green, while `derive` writes
        `detector=<name>` on every production claim, so it is live everywhere
        except here.
        """
        (self.tmp / "detectors").mkdir()
        det = self.tmp / "detectors" / "d.py"
        det.write_text("# raises when the thing is present\n")
        sha = hashing.file_sha(det)
        ledger.insert(self.conn, "claim", id="cdet", task_id="t1", kind="ext",
                      question="q", subject_refs=[], checker="ext",
                      origin="derive", detector="d.py", detector_sha=sha,
                      created_at="2026")
        ledger.append_attempt(
            self.conn, claim_id="cdet", subject_digest={}, checker_sha="sha",
            config_sha="cfg", head_commit=hashing.worktree_digest(self.tmp),
            worktree=str(self.tmp), argv=["x"], exit_code=0, stdout="", stderr="",
            started_at="2026", ended_at="2026", duration_ms=1)
        row = self.conn.execute("SELECT * FROM claim WHERE id='cdet'").fetchone()

        def st():
            return state.claim_state(self.conn, self.tmp, row, kinds_cfg=self.KINDS,
                                     config_sha="cfg", checker_sha_of=lambda _: "sha")

        self.assertEqual(st(), state.ANSWERED)
        det.write_text("# narrowed: now raises for fewer things\n")
        self.assertEqual(
            st(), state.STALE,
            "the rule that decides a claim exists was narrowed and what it had "
            "already raised stayed answered under the old rule")

    def test_head_alone_would_have_missed_it(self):
        """Names the old key so its failure is a fact, not a memory."""
        before = runner.head_commit(self.tmp)
        (self.tmp / "app.py").write_text("def notify():\n    return send_everything()\n")
        self.assertEqual(runner.head_commit(self.tmp), before)
        self.assertNotEqual(hashing.worktree_digest(self.tmp), before)

    def test_a_signature_expires_when_the_worktree_moves(self):
        """Otherwise signing is cheaper than passing, and permanently so."""
        key = state._staleness_key(
            self.conn, self.tmp,
            self.conn.execute("SELECT * FROM claim WHERE id='ctest'").fetchone(),
            kinds_cfg=self.KINDS, config_sha="cfg", checker_sha_of=lambda _: "sha")
        ledger.insert(self.conn, "accepted_risk", claim_id="ctest", kind="unprovable",
                      who="h@x", why="w", was_tty=1, git_record=".v4/risks/ctest.json",
                      subject_digest=key, created_at="2026")
        self.assertEqual(self._state(), state.RISK_ACCEPTED)

        (self.tmp / "app.py").write_text("def notify():\n    return send_everything()\n")
        self.assertNotEqual(self._state(), state.RISK_ACCEPTED)


class Signing(unittest.TestCase):
    """The one place a person is required.  DESIGN.md 2.2."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps({
            "test_command": "true", "policy": "allow_accepted_risk",
            "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({
            "test": {"question_template": "q", "checker": "test", "staleness": "repo"}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        self.cfg = config.RepoConfig(self.tmp)
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        ledger.insert(self.conn, "claim", id="c", task_id="t", kind="test", question="q",
                      subject_refs=[], checker="test", origin="derive", created_at="2026")
        # Which terminal this class is asserting about is stated, not inherited.
        # Every test below starts from "no terminal", which is the shape a CI
        # run and an agent's shell both have; `_terminal(True)` is how the two
        # tests about a person say so.
        self._stdin = _Stdin(False)
        patch = unittest.mock.patch.object(sys, "stdin", self._stdin)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _terminal(self, yes: bool):
        self._stdin.tty = yes

    def _claim(self):
        return self.conn.execute("SELECT * FROM claim WHERE id='c'").fetchone()

    def _rows(self, claim_id="c"):
        return self.conn.execute(
            "SELECT * FROM accepted_risk WHERE claim_id = ? ORDER BY id",
            (claim_id,)).fetchall()

    def _state(self):
        # The same resolver risk.accept uses; two different ones would compare
        # two different keys and the test would be checking nothing.
        return state.claim_state(self.conn, self.tmp, self._claim(),
                                 kinds_cfg=self.cfg.kinds, config_sha=self.cfg.sha,
                                 checker_sha_of=self.cfg.checker_sha_on_disk)

    WHY = "This cannot be proved mechanically and I am accepting it deliberately."

    def test_a_non_terminal_stdin_is_refused(self):
        with self.assertRaises(risk.RefusedToSign) as cm:
            risk.accept(self.conn, self.cfg, claim_id="c", kind="unprovable", why=self.WHY)
        self.assertIn("friction, not a boundary", str(cm.exception))

    def test_the_refusal_names_the_route_that_records_who_signed(self):
        """The refusal named the pty and not the flag, and they are not equal.

        A pty makes `is_tty` true, so the record says `signed_by: person` for a
        signature no person gave; `--no-tty-check` records `agent` and a note
        saying `who` is not evidence. Measured on an adopter: a worker read this
        text, concluded only a human at a terminal could proceed, and abandoned
        a task whose fix was already written. Naming only the bypass that makes
        the record lie is worse than naming neither.
        """
        with self.assertRaises(risk.RefusedToSign) as cm:
            risk.accept(self.conn, self.cfg, claim_id="c", kind="unprovable", why=self.WHY)
        text = str(cm.exception)
        self.assertIn("--no-tty-check", text)
        self.assertLess(text.index("--no-tty-check"), text.index("pty"),
                        "the honest route has to be the one the reader reaches first")

    def test_a_short_reason_is_refused(self):
        with self.assertRaises(risk.RefusedToSign):
            risk.accept(self.conn, self.cfg, claim_id="c", kind="unprovable",
                        why="fine", require_tty=False)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(risk.RefusedToSign):
            risk.accept(self.conn, self.cfg, claim_id="c", kind="whatever",
                        why=self.WHY, require_tty=False)

    def test_signing_makes_a_claim_terminal_and_leaves_a_file_in_the_repo(self):
        record, path = risk.accept(self.conn, self.cfg, claim_id="c", kind="unprovable",
                                   why=self.WHY, require_tty=False)
        self.assertEqual(self._state(), state.RISK_ACCEPTED)
        self.assertTrue(path.is_file())
        self.assertIs(json.loads(path.read_text())["stdin_was_a_tty"], False)

    def test_a_signature_written_without_a_terminal_says_an_agent_wrote_it(self):
        """`who` is git config either way, so a record naming a person and
        nothing else reads as that person having signed. Measured: a worker
        waived the check and signed away the claim judging its own work, and
        the file it left named the repo owner and said nothing more."""
        record, path = risk.accept(self.conn, self.cfg, claim_id="c",
                                   kind="unprovable", why=self.WHY,
                                   require_tty=False)
        on_disk = json.loads(path.read_text())
        self.assertEqual(on_disk["signed_by"], "agent")
        self.assertIn("--no-tty-check", on_disk["note"])
        self.assertNotIn("existing in a commit is the durable part",
                         on_disk["note"],
                         "the reassuring note belongs to the signature a person "
                         "actually sat through")

    def _review_claim(self):
        """A finding a monitor filed itself, which is what it may not sign."""
        ledger.insert(self.conn, "claim", id="own", task_id="t", kind="test",
                      question="q", subject_refs=[], checker="test",
                      origin="review", created_at="2026")
        return "own"

    def test_a_monitor_signs_what_a_detector_raised(self):
        """The relationship the outright refusal also blocked: a claim raised by
        a program, judged by a session that did not write the code.

        Every assertion here used to read the dict `accept` returns, and that
        dict is built four statements before anything is written. So a monitor
        branch that wrote the record file and skipped its `accepted_risk`
        insert -- signed, and never settled -- kept the whole suite green. The
        three places the value has to arrive are the return, the committed file
        and the row, and the fourth is that the claim actually went terminal.
        """
        record, path = risk.accept(self.conn, self.cfg, claim_id="c",
                                   kind="unprovable", why=self.WHY,
                                   as_monitor=True)
        self.assertEqual(record["signed_by"], risk.MONITOR)
        self.assertEqual(json.loads(path.read_text())["signed_by"], risk.MONITOR)
        rows = self._rows()
        self.assertEqual(len(rows), 1, "a signature that settles nothing is not one")
        self.assertEqual(rows[0]["signed_by"], risk.MONITOR)
        self.assertEqual(self._state(), state.RISK_ACCEPTED)

    def test_and_not_the_finding_it_filed_itself(self):
        """`.github/monitor/SCOPE.md`'s rule, enforced instead of asked for:
        raising and settling belong to different sessions. `v4 review add`
        writes `origin = review`, so a hand-raised claim is unsignable with the
        flag -- by the column, not by the discipline.

        The refusal used to be asserted by its text alone. `accept` raises
        before it writes, and nothing said so: move the guard below the record
        write and this passed while `.v4/risks/own.json` sat on disk, naming a
        signer, for the very finding a monitor had filed. What the refusal has
        to mean is that nothing happened.
        """
        own = self._review_claim()
        with self.assertRaises(risk.RefusedToSign) as cm:
            risk.accept(self.conn, self.cfg, claim_id=own, kind="unprovable",
                        why=self.WHY, as_monitor=True)
        self.assertIn("raised by hand", str(cm.exception))
        self.assertEqual(list((self.tmp / ".v4" / "risks").glob("**/*.json")), [],
                         "a refusal that leaves a signature record is not one")
        self.assertEqual(self._rows(own), [])

    def test_and_not_another_reviewer_s_finding_either(self):
        """The input the old refusal told a lie about.

        It said "`origin = review`, so it is yours, so you cannot sign it", and
        `origin` records how a claim was made, not who made it: a finding filed
        by a different session is the same row. The refusal is right and its
        reason was not, so what it says now is what it can know -- raised by
        hand, and nothing records whose hand.
        """
        own = self._review_claim()
        text = ""
        with self.assertRaises(risk.RefusedToSign) as cm:
            risk.accept(self.conn, self.cfg, claim_id=own, kind="unprovable",
                        why=self.WHY, as_monitor=True)
        text = str(cm.exception)
        self.assertNotIn("so it is yours", text)
        self.assertIn("how a claim was made", text)
        self.assertIn("not who made it", text)

    def test_a_widen_claim_is_hand_raised_too(self):
        """`origin` has three values and the guard is an allow-list of one.

        A `widen` claim is filed by whoever ran `v4 scope widen`, which is a
        hand, so it falls on the same side as a review finding. Written down
        because the constant is an allow-list: if it were a deny-list of
        `('review',)` this would pass through, and the failure would be a
        monitor signing away somebody's reach into a protected path.
        """
        ledger.insert(self.conn, "claim", id="w", task_id="t", kind="test",
                      question="q", subject_refs=[], checker="test",
                      origin="widen", created_at="2026")
        self.assertNotIn("widen", risk.RAISED_BY_A_PROGRAM)
        with self.assertRaises(risk.RefusedToSign):
            risk.accept(self.conn, self.cfg, claim_id="w", kind="unprovable",
                        why=self.WHY, as_monitor=True)
        self.assertEqual(self._rows("w"), [])

    def test_a_monitor_is_not_asked_for_a_terminal(self):
        """The role exists to be a second agent, so requiring a tty asks it for
        the one thing it is not. `require_tty` defaults True and is not passed
        here, which is what a caller that forgot the flag would do."""
        record, _path = risk.accept(self.conn, self.cfg, claim_id="c",
                                    kind="unprovable", why=self.WHY,
                                    as_monitor=True)
        self.assertIs(record["stdin_was_a_tty"], False)

    def test_the_record_says_what_the_flag_does_not_prove(self):
        """`v4` is a subprocess the agent starts and nothing tells it which
        session that is, so this is friction and the file has to say so rather
        than let `signed_by: monitor` read as verified."""
        record, _path = risk.accept(self.conn, self.cfg, claim_id="c",
                                    kind="unprovable", why=self.WHY,
                                    as_monitor=True)
        self.assertIn("friction rather than a boundary", record["note"])
        # It names `--no-tty-check` on purpose, to say it has the same
        # standing. What it must not be is that route's note: those two say
        # different things about who is answerable.
        other, _p = risk.accept(self.conn, self.cfg, claim_id="c",
                                kind="no_checker", why=self.WHY,
                                require_tty=False)
        self.assertNotEqual(record["note"], other["note"])
        self.assertIn("did not raise this claim", record["note"])

    def test_the_three_routes_are_told_apart(self):
        """Two values could not: `agent` meant only `not a person`, so a worker
        waiving the claim that judges its own work and a second session signing
        a detector's claim left the identical record.

        All three, each with the terminal it actually runs with, and read back
        from the ledger rather than from the return value -- the row is what
        `v4 ship` and `v4 trend` count, and for a day it was the one place the
        third value did not reach.

        This read the ambient `sys.stdin.isatty()` until 2026-08-27, so under a
        pty the worker route recorded `person` and the assertion below failed.
        """
        worker, _p = risk.accept(self.conn, self.cfg, claim_id="c",
                                 kind="unprovable", why=self.WHY,
                                 require_tty=False)
        self.assertEqual(worker["signed_by"], risk.AGENT)
        monitor, _p2 = risk.accept(self.conn, self.cfg, claim_id="c",
                                   kind="no_checker", why=self.WHY,
                                   as_monitor=True)
        self._terminal(True)
        person, _p3 = risk.accept(self.conn, self.cfg, claim_id="c",
                                  kind="baseline_raise", why=self.WHY)
        self.assertEqual(
            [worker["signed_by"], monitor["signed_by"], person["signed_by"]],
            [risk.AGENT, risk.MONITOR, risk.PERSON])
        self.assertEqual([r["signed_by"] for r in self._rows()],
                         [risk.AGENT, risk.MONITOR, risk.PERSON])
        # And `was_tty` cannot stand in for it: two of the three share a value.
        self.assertEqual([r["was_tty"] for r in self._rows()], [0, 0, 1])

    def test_a_signature_expires_when_the_bytes_move(self):
        """Or signing is permanent where passing is not, and becomes the cheaper move."""
        risk.accept(self.conn, self.cfg, claim_id="c", kind="unprovable",
                    why=self.WHY, require_tty=False)
        self.assertEqual(self._state(), state.RISK_ACCEPTED)
        (self.tmp / "a.py").write_text("x = 2  # different bytes\n")
        self.assertNotEqual(self._state(), state.RISK_ACCEPTED)


class TheOperatingLoopReachesTheAdopter(unittest.TestCase):
    """23 checkers, 9 detectors, 3 hooks -- and no way to run any of it.

    `/run` and the four roles SPEC §12.5 names stayed in the framework for as
    long as `install` existed. The only way to work in an adopter was to write
    the loop into a prompt by hand, which means copying `task-splitter`'s
    splitting rule and `worker`'s "you may not ship yourself" into a fourth
    place where they drift from the three that already say them.
    """

    def _pair(self):
        import tempfile
        src = Path(tempfile.mkdtemp()); dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, src, ignore_errors=True)
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        for d in ("checkers", "detectors", "hooks", ".v4/lenses",
                  ".claude/agents", ".claude/commands"):
            (src / d).mkdir(parents=True, exist_ok=True)
        (src / ".v4" / "checkers.json").write_text("{}")
        (src / ".v4" / "claim_kinds.json").write_text("{}")
        (src / ".v4" / "detectors.json").write_text("{}")
        (src / ".claude" / "agents" / "worker.md").write_text("你答 claim。\n")
        (src / ".claude" / "commands" / "run.md").write_text("# /run\n")
        return src, dst

    def test_the_agents_and_the_command_are_installed(self):
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, {})
        self.assertTrue((dst / ".claude" / "agents" / "worker.md").is_file(),
                        "an adopter with no roles has no way to run anything")
        self.assertTrue((dst / ".claude" / "commands" / "run.md").is_file())

    def test_a_role_the_repo_rewrote_is_left_alone(self):
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, {})
        (dst / ".claude" / "agents" / "worker.md").write_text("mine\n")
        (src / ".claude" / "agents" / "worker.md").write_text("v2\n")
        out = dict(install.copy_files(src, dst, {}))
        self.assertEqual(out[".claude/agents/worker.md"], "yours")
        self.assertEqual((dst / ".claude" / "agents" / "worker.md").read_text(),
                         "mine\n")


class TheProgramIncludesTheHalfThatDecides(unittest.TestCase):
    """In an adopter the checker is copied in and `kernel/analysis/` is not.

    20 of 27 checkers are a thin CLI over a module there -- the one that
    actually decides. Following same-repo imports only hashed the argument
    parser and skipped the judgement: exactly the failure `program_sha` was
    written to fix, fixed in this repo and left standing in every repo that
    adopts it.
    """

    def _adopter(self):
        import tempfile
        fw = Path(tempfile.mkdtemp()); ad = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, fw, ignore_errors=True)
        self.addCleanup(shutil.rmtree, ad, ignore_errors=True)
        (fw / "kernel" / "analysis").mkdir(parents=True)
        (fw / "kernel" / "__init__.py").write_text("")
        (fw / "kernel" / "analysis" / "__init__.py").write_text("")
        (fw / "kernel" / "analysis" / "thing.py").write_text("def decide():\n    return 1\n")
        (ad / "checkers").mkdir()
        (ad / "checkers" / "c.py").write_text(
            "from kernel.analysis import thing\nprint(thing.decide())\n")
        (ad / ".v4").mkdir()
        (ad / ".v4" / "home").write_text(str(fw) + "\n")
        return fw, ad

    def test_editing_the_deciding_module_moves_the_sha(self):
        from kernel import hashing
        fw, ad = self._adopter()
        entry = ad / "checkers" / "c.py"
        before = hashing.program_sha(ad, entry)
        m = fw / "kernel" / "analysis" / "thing.py"
        m.write_text("def decide():\n    return 2\n")
        self.assertNotEqual(hashing.program_sha(ad, entry), before,
                            "the module that decides is part of the program")

    def test_an_unrelated_framework_file_does_not(self):
        from kernel import hashing
        fw, ad = self._adopter()
        entry = ad / "checkers" / "c.py"
        before = hashing.program_sha(ad, entry)
        (fw / "kernel" / "analysis" / "other.py").write_text("x = 1\n")
        self.assertEqual(hashing.program_sha(ad, entry), before,
                         "only what this checker imports, still")


class TheGoEmitterIsPartOfTheProgramToo(unittest.TestCase):
    """`_go/` is reached by subprocess, so no import walk ever found it.

    Thirteen checkers read their view of a Go file from
    `kernel/analysis/_go/shape.go`, and that file is neither a Python import nor
    a `.json` table beside a module -- the two things `program_sha` follows. So
    it fell through both.

    Measured against dadac9e: appending one comment line to `shape.go` left
    `program_sha` for `checkers/fail_closed.py` byte-identical, which means every
    Go verdict this repo has recorded stayed answered by a program that no
    longer exists. That is the failure `program_sha`'s own docstring cites as
    the reason it exists, fixed for Python imports and left standing for Go.
    """

    def setUp(self):
        self.framework = Path(tempfile.mkdtemp(prefix="v4-emitter-hash-"))
        self.addCleanup(shutil.rmtree, self.framework, ignore_errors=True)
        # The probe must not edit the framework another checker or maintenance
        # review is reading. Keep the real import closure, but give it an owner
        # whose lifetime is this test rather than the shared checkout.
        for directory in ("kernel", "checkers"):
            shutil.copytree(REPO / directory, self.framework / directory,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    def _emitter(self):
        return self.framework / "kernel" / "analysis" / "_go" / "shape.go"

    def test_editing_the_emitter_moves_a_go_reading_checker(self):
        from kernel import hashing
        src = self._emitter()
        self.assertTrue(src.is_file(), "the Go emitter is where this expects")
        orig = src.read_text(encoding="utf-8")
        self.addCleanup(src.write_text, orig)
        for who in ("checkers/fail_closed.py", "checkers/external_write.py",
                    "checkers/test_weakened.py"):
            before = hashing.program_sha(self.framework, self.framework / who)
            src.write_text(orig + "\n// probe\n")
            after = hashing.program_sha(self.framework, self.framework / who)
            src.write_text(orig)
            self.assertNotEqual(after, before,
                                f"{who} reads Go through the emitter")

    def test_a_checker_that_does_not_read_go_is_untouched(self):
        """Targeted, not blanket: `secret_scan` never imports `gosource`."""
        from kernel import hashing
        src = self._emitter()
        orig = src.read_text(encoding="utf-8")
        self.addCleanup(src.write_text, orig)
        entry = self.framework / "checkers" / "secret_scan.py"
        before = hashing.program_sha(self.framework, entry)
        src.write_text(orig + "\n// probe\n")
        after = hashing.program_sha(self.framework, entry)
        src.write_text(orig)
        self.assertEqual(after, before,
                         "a Python-only checker does not depend on the emitter")

    def test_hash_probe_leaves_an_adopters_maintenance_snapshot_unchanged(self):
        from kernel import maintenance
        root = Path(tempfile.mkdtemp(prefix="v4-emitter-observer-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        _git_repo(root)
        (root / ".v4").mkdir()
        (root / ".v4/config.json").write_text(json.dumps({
            "test_command": "python3 -m unittest", "policy": "allow_accepted_risk"}))
        (root / ".v4/claim_kinds.json").write_text("{}")
        (root / "app.py").write_text("value = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm",
                        "observer baseline"], cwd=root, check=True)

        before = maintenance.snapshot(root)
        src = self._emitter()
        orig = src.read_text(encoding="utf-8")
        self.addCleanup(src.write_text, orig)
        src.write_text(orig + "\n// probe\n")
        self.assertEqual(maintenance.snapshot(root), before,
                         "a hash test must not invalidate another adopter's review")


class TheCheapAnswersComeFirst(unittest.TestCase):
    """A repo-scoped claim is keyed on the content of the tree, so any edit
    expires it. Running the 567-second one before the 0.1-second ones means
    every failure among them is an edit, and the 567 seconds were spent before
    the answer they produced could be true.

    Measured across one day on the reference adopter: `test` ran 18 times for
    143 minutes, and 25 of those minutes were spent while a claim answerable
    only by editing a file was still failing -- void before they started.
    """

    def test_cost_comes_from_what_it_actually_took(self):
        from kernel import lifecycle, ledger
        import tempfile, subprocess
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        conn = ledger.connect(tmp)
        self.assertEqual(lifecycle.kind_cost(conn, "never-run"), 0,
                         "a kind nobody has run reads as cheap")

        ledger.insert(conn, "task", id="t", request="r", scope_globs="[]",
                      base_commit="c", created_at="n")
        for i, ms in enumerate((1000, 90000, 95000)):
            ledger.insert(conn, "claim", id=f"c{i}", task_id="t", kind="slow",
                          question="q", file="", symbol="", variant="",
                          subject_refs="[]", checker="s", detector="",
                          detector_sha="", origin="detector", created_at="n")
            ledger.append_attempt(conn, claim_id=f"c{i}", exit_code=0, stdout="",
                                  stderr="", argv="[]", duration_ms=ms,
                                  subject_digest="{}", checker_sha="x",
                                  config_sha="y", worktree="/", head_commit="h",
                                  started_at="n", ended_at="n")
        self.assertEqual(lifecycle.kind_cost(conn, "slow"), 90000,
                         "the median, not the newest and not the worst")
        self.assertGreater(lifecycle.kind_cost(conn, "slow"),
                           lifecycle.EXPENSIVE_MS)


class ATaskHasASecondEnding(unittest.TestCase):
    """`ship` is one ending. Abandoning is the other, and it was missing.

    Until the hooks inferred their task, an unshipped task was inert -- nothing
    read it. They read it now: the newest unended task is the scope every write
    is checked against. A throwaway task opened to probe something therefore
    became the permanent guard, and the only way out was to ship work that was
    never done.
    """

    def _repo(self):
        import tempfile, subprocess
        from kernel import config, ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8}}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        return ledger.connect(tmp), config.RepoConfig(tmp), tmp

    def test_an_abandoned_task_stops_being_the_open_one(self):
        from kernel import ledger, lifecycle
        conn, cfg, tmp = self._repo()
        lifecycle.open_task(conn, cfg, task_id="t-old", request="r",
                            scope_globs=["a/**"])
        lifecycle.open_task(conn, cfg, task_id="t-probe", request="r",
                            scope_globs=["b/**"])
        self.assertEqual(ledger.open_task_id(conn), "t-probe")

        ledger.insert(conn, "event", task_id="t-probe", claim_id=None,
                      kind="abandoned", actor="worker",
                      payload={"why": "x" * 50}, created_at="n")
        self.assertEqual(ledger.open_task_id(conn), "t-old",
                         "an abandoned task must not go on guarding")

    def test_shipping_ends_it_too(self):
        from kernel import ledger, lifecycle
        conn, cfg, tmp = self._repo()
        lifecycle.open_task(conn, cfg, task_id="t-1", request="r",
                            scope_globs=["a/**"])
        ledger.insert(conn, "event", task_id="t-1", claim_id=None,
                      kind="shipped", actor="kernel", payload={}, created_at="n")
        self.assertIsNone(ledger.open_task_id(conn))


class ReadingNothingIsNotReadingClean(unittest.TestCase):
    """Four places where "I could not look" and "there is nothing" were one value.

    Each is a different program and the same defect: a read that fails returns
    the empty answer, and the empty answer is the one that means everything is
    fine. The direction matters -- the other way round costs a false alarm, this
    way costs the gate.
    """

    def test_the_scope_checker_refuses_a_diff_it_could_not_take(self):
        """`git diff --name-only <unresolvable>` exits 128 with empty stdout, so
        the program that issues the protected-path verdict printed "no changes"
        and exited 0 from a diff it never read."""
        checker = REPO / "checkers" / "scope.py"
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)

        def run(base):
            subject = tmp / f"subject-{base[:8]}.json"
            subject.write_text(json.dumps(
                {"repo_root": str(tmp), "diff_base": base,
                 "params": {"scope_globs": ["**"]}}))
            return subprocess.run(
                [sys.executable, str(checker), "--subject", str(subject)],
                cwd=tmp, capture_output=True, text=True)

        self.assertEqual(run("HEAD").returncode, 0, "an empty diff is still PASS")
        bad = run("deadbeef" * 5)
        self.assertEqual(bad.returncode, 4, bad.stdout + bad.stderr)
        self.assertIn("CANNOT VERIFY", bad.stderr)

    def test_a_syntax_error_is_a_crash_not_a_finding(self):
        """CPython's parser reports a `SyntaxError` itself, so stderr carries no
        `Traceback` header -- and the one crash a worker produces by editing a
        checker was recorded as FAIL, which is a real finding."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        broken = tmp / "broken.py"
        broken.write_text("def f(:\n    pass\n")
        r = subprocess.run([sys.executable, str(broken)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("Traceback (most recent call last):", r.stderr,
                         "the premise: the parser does not use the header")
        self.assertTrue(runner.reported_nothing(r.returncode, r.stdout, r.stderr))

    def test_a_real_finding_is_still_a_finding(self):
        """The control. Without it, a predicate that always says `True` passes
        the test above."""
        self.assertFalse(runner.reported_nothing(1, "3 violations", ""))
        self.assertFalse(runner.reported_nothing(0, "", ""))

    def test_a_tracked_file_list_that_could_not_be_read_says_so(self):
        """`[]` reads as "no dead globs" and lets every facts-reading detector
        through, in the guard against a filter that turns one off silently."""
        tmp = Path(tempfile.mkdtemp())          # not a git repo
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        out = derive._filters_matching_nothing(tmp, {"ui_globs": ["web/**"]})
        self.assertTrue(out, "a git failure is not a clean table")
        self.assertIn("could not be read", out[0])

    def test_a_credential_guard_that_did_not_run_is_audible(self):
        """The sentence goes into an append-only table and then into a committed
        file, so a guard that silently stood down is found hours later."""
        from kernel.analysis import secret_patterns

        def boom(*a, **k):
            raise RuntimeError("the classifier fell over")

        err = io.StringIO()
        with unittest.mock.patch.object(sys, "stderr", err), \
             unittest.mock.patch.object(secret_patterns, "analyse_source", boom):
            self.assertIsNone(engagement._credential_in("some sentence"))
        self.assertIn("credential guard", err.getvalue())
        self.assertIn("fell over", err.getvalue(),
                      "and it names what went wrong, not just that something did")


class AnExportCarriesItsOwnAnchor(unittest.TestCase):
    """The walk CI runs could not see rows dropped off the end.

    `audit_chain` grew `.v4/chain_head.json` for exactly that -- a shorter
    chain is internally perfect at every link -- and `verify_exported`, the
    only walk that can run in CI (the ledger lives in `.git/`), was never given
    an equivalent. Truncating the file printed "chain intact across N attempts".

    The anchor could not simply be read from `.v4/chain_head.json` either: that
    file is refreshed by every `v4 check` and the export is written only by
    `ship`, so the two are snapshots of different moments. CI compared them and
    failed all six runs at "export has 72 attempts, anchor records 81" -- the
    ordinary gap between a ship and the checks after it, reported as tampering.
    """

    def _ledger(self, n):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        with ledger.writing(conn):
            ledger.insert(conn, "task", id="t", request="r",
                          scope_globs='["**"]', base_commit="x",
                          created_at="2026")
            for i in range(n):
                ledger.insert(conn, "claim", id=f"c{i}", task_id="t", kind="k",
                              question="q", subject_refs="[]", checker="ch",
                              origin="derive", created_at="2026")
        for i in range(n):
            ledger.append_attempt(
                conn, claim_id=f"c{i}", subject_digest="{}", checker_sha="s",
                config_sha="cf", head_commit="hc", worktree="w", argv="[]",
                exit_code=0, stdout=f"out {i}", stderr="", started_at="2026",
                ended_at="2026", duration_ms=1,
                claim_digest=ledger.claim_digest(conn, f"c{i}"), facts_sha="")
        out = tmp / "export.jsonl"
        ledger.export_jsonl(conn, out, tmp)
        return out

    def test_an_untouched_export_walks_clean(self):
        n, problems = ledger.verify_exported(self._ledger(3))
        self.assertEqual((n, problems), (3, []))

    def test_rows_removed_from_the_end_are_caught(self):
        path = self._ledger(3)
        kept = [l for l in path.read_text().splitlines()
                if not (json.loads(l).get("_table") == "attempt"
                        and json.loads(l)["id"] == 3)]
        path.write_text("\n".join(kept) + "\n")
        n, problems = ledger.verify_exported(path)
        self.assertEqual(n, 2)
        self.assertTrue(any("anchor" in p for p in problems), problems)

    def test_an_export_with_no_anchor_says_it_cannot_answer(self):
        """Every export written before this one. Silence there would be the
        same output as a complete chain, which is what it was."""
        path = self._ledger(3)
        kept = [l for l in path.read_text().splitlines()
                if json.loads(l).get("_table") != ledger.ANCHOR_TABLE]
        path.write_text("\n".join(kept) + "\n")
        _, problems = ledger.verify_exported(path)
        self.assertTrue(any("no chain anchor" in p for p in problems), problems)


class AConstantIsNotAStalenessKey(unittest.TestCase):
    """`worktree_digest` returned `sha256("")` wherever git could not answer.

    `tree_state` returns `{}` there and hashing nothing gave a fixed constant --
    identical for every such directory and unmoved by any edit. Measured in a
    temp dir: adding a file left the digest equal to
    `hashlib.sha256().hexdigest()`, so every repo-scoped claim keyed on a value
    that could never go stale. `tree_state`'s own docstring says "the one caller
    that must still work there says so"; this was that caller.
    """

    def _dir(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        return tmp

    def test_a_file_appearing_moves_it(self):
        tmp = self._dir()
        before = hashing.worktree_digest(tmp)
        (tmp / "x.py").write_text("x = 1\n")
        self.assertNotEqual(hashing.worktree_digest(tmp), before)

    def test_content_changing_moves_it(self):
        tmp = self._dir()
        (tmp / "x.py").write_text("x = 1\n")
        before = hashing.worktree_digest(tmp)
        (tmp / "x.py").write_text("x = 2\n")
        self.assertNotEqual(hashing.worktree_digest(tmp), before)

    def test_two_directories_with_the_same_content_agree(self):
        """Content, not path: the digest is what the tree holds."""
        a, b = self._dir(), self._dir()
        for d in (a, b):
            (d / "x.py").write_text("x = 1\n")
        self.assertEqual(hashing.worktree_digest(a), hashing.worktree_digest(b))


class TheCheckoutDirectoryNameDecidesNothing(unittest.TestCase):
    """`control_plane_budget` matched its exclusions against the absolute path.

    `f` comes from `root.rglob`, so `f.parts` carries every ancestor: a repo
    checked out under any directory named `tests`, `docs`, `build`, `dist`,
    `node_modules` or `.git` measured zero statements, reported total 0 against
    its ceiling, and passed forever -- the check-that-cannot-fail this checker
    exists to prevent, in the checker.
    """

    def _measure(self, parent):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "cpb", REPO / "checkers" / "control_plane_budget.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = tmp / parent / "proj"
        (root / "kernel").mkdir(parents=True)
        (root / "kernel" / "a.py").write_text("x = 1\ny = 2\nz = 3\n")
        (root / "tests").mkdir()
        (root / "tests" / "t.py").write_text("q = 1\n")
        # A git repo, because `measure` asks `git ls-files` rather than walking
        # -- which is what stops it charging an adopter for `.venv` and for the
        # red fixtures `v4 install` copied in.
        _git_repo(root)
        (root / ".venv").mkdir()
        (root / ".venv" / "lib.py").write_text("\n".join(f"v{i} = 1" for i in range(50)))
        (root / ".gitignore").write_text(".venv/\n")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=root, check=True)
        return mod.measure(root)

    def test_an_ordinary_parent(self):
        self.assertEqual(self._measure("myrepo"), {"kernel": 3})

    def test_a_parent_named_after_an_excluded_directory(self):
        for parent in ("build", "dist", "tests", "docs", "node_modules"):
            self.assertEqual(self._measure(parent), {"kernel": 3}, parent)


def _load_module(path):
    """One program, imported for what it binds rather than read for what it says."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"_probe_{path.stem}_{abs(hash(str(path)))}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class EveryProgramHasSomethingAFrameCanBeNamedAfter(unittest.TestCase):
    """A module-level script cannot be the subject of a review finding.

    `review.resolve_symbol` refuses a `--symbol` that is not a function in the
    file, and a finding raised with no symbol has nothing for `redgreen` to
    trace -- so for a script the only exit left is a signature, which is the
    outcome that function exists to prevent, one level out.

    Twenty-two programs were scripts: fifteen `detectors/always_*.py`, both
    `checkers/_selftest_*.py`, `checkers/test.py` and four detectors. Twenty-six
    siblings already had `def main()`, so this is the convention catching up
    with itself rather than a new rule.
    """

    def test_no_program_is_a_bare_script(self):
        import ast
        bare = []
        for d in ("detectors", "checkers"):
            for f in sorted((REPO / d).glob("*.py")):
                tree = ast.parse(f.read_text(encoding="utf-8"))
                if not any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                           for n in tree.body):
                    bare.append(str(f.relative_to(REPO)))
        self.assertEqual(bare, [], "\n".join(
            [f"{p}: defines no function, so no finding on it can ever close"
             for p in bare]))

    def test_the_registry_agrees_with_what_is_on_disk(self):
        """The other half of the same change: editing a registered program
        moves its `checker_sha`, and `runner` refuses to run one that has moved
        (`exit 6 CHECKER_TAMPERED`). A wrap that skipped re-registration would
        turn every claim it serves into a refusal, one at a time."""
        stale = []
        for name, reg in (("checker", ".v4/checkers.json"),
                          ("detector", ".v4/detectors.json")):
            table = json.loads((REPO / reg).read_text())
            for key, entry in sorted(table.items()):
                path = REPO / (entry.get("path") or "")
                if not entry.get("sha256") or not path.is_file():
                    continue
                if hashing.file_sha(path) != entry["sha256"]:
                    stale.append(f"{name} {key}: {reg} is not what is on disk")
        self.assertEqual(stale, [], "\n".join(stale))

    def test_every_kind_still_has_a_registered_checker(self):
        """The other way a registry goes wrong, and the one "stale sha" cannot see.

        `register()` *removes* the entry when the fixtures do not hold, and says
        why: a checker that passed once used to stay registered through every
        later refusal, and 21 of 23 kinds were refused on a real adoption while
        every one kept running. So a refused re-registration leaves no stale
        hash to find -- it leaves a kind pointing at nothing, which is what
        happened here when a checker was edited with a name it did not define.
        """
        kinds = json.loads((REPO / ".v4" / "claim_kinds.json").read_text())
        reg = json.loads((REPO / ".v4" / "checkers.json").read_text())
        orphan = sorted(k for k, v in kinds.items()
                        if v.get("checker") and v["checker"] not in reg)
        self.assertEqual(orphan, [], "\n".join(
            f"kind {k!r} names checker {kinds[k]['checker']!r}, "
            f"which is not registered" for k in orphan))


class OneProtectedSet(unittest.TestCase):
    """Six copies of "which paths judge the work", and one of them had drifted.

    `scope.PROTECTED_DEFAULT`, `.v4/config.json`, `.v4/facts.<repo>.json`,
    `init.CONFIG_TEMPLATE`, `facts.propose` and
    `external_write._DEFAULT_TABLE` each stated it. The built-in table held
    three of the four and omitted `.github/**` -- the directory holding the
    workflow and the monitor contract -- and `table_from` returns that table
    for every repo that ships no facts file, which is the path `register`
    takes when it runs a checker's fixtures without `--facts`. `propose` wrote
    a fourth value, `[".v4/**"]`, into every adopter's drafted table.

    Asserted against the constant rather than against a literal: a test that
    restates the four globs is a seventh copy.
    """

    def test_the_built_in_table_is_the_owners_list(self):
        from kernel.analysis.external_write import _DEFAULT_TABLE
        self.assertEqual(_DEFAULT_TABLE["protected_paths"],
                         list(_subject_files.PROTECTED_DEFAULT))

    def test_a_proposed_table_is_the_owners_list(self):
        from kernel import facts as facts_mod
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)
        self.assertEqual(facts_mod.propose(tmp)["protected_paths"],
                         list(_subject_files.PROTECTED_DEFAULT))

    def test_the_scaffold_writes_only_what_the_repo_adds(self):
        """A copy here is a line an adopter can delete with no effect, because
        `protected_for` unions rather than reads."""
        from kernel import init as init_mod
        self.assertEqual(init_mod.CONFIG_TEMPLATE["protected_paths"], [])

    def test_the_union_is_written_once(self):
        from kernel import config as config_mod, scope as scope_mod
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "protected_paths": ["ops/**"]}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        want = sorted(set(_subject_files.PROTECTED_DEFAULT) | {"ops/**"})
        self.assertEqual(config_mod.RepoConfig(tmp).protected, want)
        self.assertEqual(
            scope_mod.protected_for({"protected_paths": ["ops/**"]}), want,
            "checkers/scope.py reads through this, not through RepoConfig")

    def test_declaring_nothing_still_protects_the_framework(self):
        from kernel import scope as scope_mod
        self.assertEqual(scope_mod.protected_for({}),
                         sorted(_subject_files.PROTECTED_DEFAULT))
        self.assertEqual(scope_mod.protected_for(None),
                         sorted(_subject_files.PROTECTED_DEFAULT))


class OneGlobMatcher(unittest.TestCase):
    """Every program that asks "does this path match this glob" gets one answer.

    There were six and they disagreed. Each row below was measured as a split
    before `subject_files.matches` existed: `subject_files` said one thing and
    `lifecycle._files_in_scope`, `checkers/scope.matches`, `kernel/scope._matches`,
    `write_block.in_scope` and `shell_command._protected` said the other, in the
    program that issues the verdict as often as in the ones that feed it.

    The five rules are asserted here rather than in each caller, because a
    seventh copy is the failure and a test per caller would not see it.
    """

    #: A plain reference, not a class-body import: an imported function
    #: becomes a bound method and swallows `self` as its first argument.
    M = staticmethod(_subject_files.matches)

    def test_a_glob_as_written(self):
        self.assertTrue(self.M("app/x.py", ["app/x.py"]))

    def test_a_directory_reaches_what_is_under_it(self):
        for g in ("app", "app/"):
            self.assertTrue(self.M("app/x.py", [g]), g)

    def test_a_double_star_reaches_one_level(self):
        self.assertTrue(self.M("app/x.py", ["app/**"]))

    def test_a_leading_double_star_reaches_the_root(self):
        """git reads `**/x` as including `x` at the top. Five of the six did
        not, so `**/requirements*.txt` missed a top-level `requirements.txt`
        and this repo's own `**/__pycache__/**` missed a top-level one."""
        self.assertTrue(self.M("requirements.txt", ["**/requirements*.txt"]))
        self.assertTrue(self.M("__pycache__/a.pyc", ["**/__pycache__/**"]))
        self.assertTrue(self.M("x.py", ["**/*.py"]))

    def test_a_glob_covers_the_directory_it_names(self):
        """`rm -rf .v4/` was refused and `rm -rf .v4` was allowed."""
        self.assertTrue(self.M(".v4", [".v4/**"]))
        self.assertTrue(self.M("checkers", ["checkers/**"]))

    def test_a_leading_dot_slash_is_the_same_path(self):
        self.assertTrue(self.M("./.v4/config.json", [".v4/**"]))

    def test_it_still_says_no(self):
        self.assertFalse(self.M("docs/x.md", ["app/**"]))
        self.assertFalse(self.M("application/x.py", ["app/**"]))

    def test_every_caller_gives_the_owner_s_answer(self):
        """The property a seventh copy would break, asserted on the split that
        found the other six."""
        from kernel import scope as scope_mod
        from kernel.analysis import shell_command, subject_files
        rel, globs = "x.py", ["**/*.py"]
        self.assertTrue(subject_files.matches(rel, globs))
        for name, fn in (("scope._matches", scope_mod._matches),
                         ("shell_command._protected", shell_command._protected),
                         ("subject_files.excluded", subject_files.excluded)):
            self.assertTrue(fn(rel, globs), name)


class WideningIsJudgedOnWhatItReaches(unittest.TestCase):
    """`--add **` grants every protected path and used to report none.

    `widen` asked whether the glob *string* looked like a protected path, so
    `.v4/**` was reported and `**` was not -- the trailing characters the
    caller happened to type decided whether the signature instruction printed.
    """

    def _repo(self):
        from kernel import config, ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8}}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / "app").mkdir()
        (tmp / "app" / "x.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)
        return ledger.connect(tmp), config.RepoConfig(tmp)

    WHY = ("this widen is about .v4 and app and the reason has to run past the "
           "forty character floor to be accepted at all")

    def test_widening_to_everything_reports_the_protected_paths(self):
        from kernel import lifecycle, scope as scope_mod
        conn, cfg = self._repo()
        self.addCleanup(conn.close)
        lifecycle.open_task(conn, cfg, task_id="t-w", request="r",
                            scope_globs=["app/**"])
        _, hits = scope_mod.widen(conn, cfg, task_id="t-w", add=["**"],
                                  why=self.WHY)
        self.assertEqual(hits, ["**"],
                         "`**` covers .v4/config.json, which judges this work")

    def test_a_widen_that_reaches_nothing_protected_reports_nothing(self):
        from kernel import lifecycle, scope as scope_mod
        conn, cfg = self._repo()
        self.addCleanup(conn.close)
        lifecycle.open_task(conn, cfg, task_id="t-w", request="r",
                            scope_globs=["docs/**"])
        _, hits = scope_mod.widen(conn, cfg, task_id="t-w", add=["app/**"],
                                  why=self.WHY)
        self.assertEqual(hits, [], "app/ holds nothing that judges the work")


class AWidenedPathReachesTheGate(unittest.TestCase):
    """`v4 scope widen` says a file belongs here. The gate has to hear it.

    Widening is recorded as an event, not by mutating `task.scope_globs` -- the
    ledger is append-only. `scope.current_scope()` unions the two and is what
    `v4 scope show` reads. Both `lifecycle.derive` and the subject handed to
    every checker re-derived the scope from the task row alone, so a widened
    path stayed outside scope for everything that judges.

    Measured on a real task: `v4 scope widen --add CLAUDE.md` printed a
    three-path scope, `v4 scope show` agreed, and `scope` went on reporting
    CLAUDE.md as outside a two-path scope. The one command whose purpose is to
    say "this belongs here" did not reach the gate that asks.
    """

    def _repo(self):
        import tempfile, subprocess
        from kernel import config, ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8}}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        return ledger.connect(tmp), config.RepoConfig(tmp)

    def test_the_subject_carries_what_widen_added(self):
        from kernel import lifecycle, scope as scope_mod
        conn, cfg = self._repo()
        lifecycle.open_task(conn, cfg, task_id="t-w", request="r",
                            scope_globs=["app/**"])
        # Naming the path is required -- `widen` judges the reason with the same
        # rules an engagement sentence gets, and refuses one that does not
        # mention what it is about.
        why = ("docs/NOTES.md records the thing this task just changed, so "
               "leaving it out would ship a note describing a state that no "
               "longer exists.")
        scope_mod.widen(conn, cfg, task_id="t-w", add=["docs/NOTES.md"], why=why)

        self.assertIn("docs/NOTES.md", scope_mod.current_scope(conn, "t-w"))
        self.assertIn("docs/NOTES.md", lifecycle._scope_now(conn, "t-w"),
                      "the checker's subject must see the widened path")

    def test_widening_onto_a_forbidden_path_is_refused_here(self):
        """`protected` is reported and signed; `forbid` has no way through.

        Measured before this: the widen succeeded, printed the wider scope and
        `protected hits: []`, said nothing about the forbidden set, and the
        refusal arrived at `v4 check`. The cheap exit exists so a worker does
        not find out twenty minutes later, and on this path it did the opposite.
        """
        from kernel import lifecycle, scope as scope_mod
        conn, cfg = self._repo()
        lifecycle.open_task(conn, cfg, task_id="t-f", request="r",
                            scope_globs=["app/**"],
                            forbid_globs=["core/loader.py"])
        why = ("core/loader.py holds the two registries this task reads, so "
               "reaching them would mean changing a file this task said it "
               "would not touch.")
        with self.assertRaises(scope_mod.WidenRefused) as c:
            scope_mod.widen(conn, cfg, task_id="t-f",
                            add=["core/loader.py"], why=why)
        self.assertIn("out of bounds", str(c.exception))
        self.assertNotIn("core/loader.py",
                         scope_mod.current_scope(conn, "t-f"),
                         "a refused widen must not have widened anything")


class RepoScopedSigning(unittest.TestCase):
    """A structural absence is one fact, so it is signed once.  DESIGN.md 2.2.

    Measured before this existed: a claim id carries its task id, so a signature
    could only ever cover one task. `spec-coverage` in a repo with no SPEC.md
    exits 4 every task forever, and 4 is deliberately non-terminal -- so eight
    tasks meant eight identical signatures for one fact that never changed.
    Signing that often is how signing stops meaning anything.
    """

    WHY = "This repo has no SPEC.md at all, so that checker has nothing to read."

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps({
            "test_command": "true", "policy": "allow_accepted_risk",
            "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({
            "spec-coverage": {"question_template": "q", "checker": "spec",
                              "staleness": "repo"},
            "external-write": {"question_template": "q", "checker": "ext",
                               "staleness": "subject"}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)
        self.cfg = config.RepoConfig(self.tmp)
        self.conn = ledger.connect(self.tmp)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _claim(self, task, cid, kind="spec-coverage", checker="spec"):
        ledger.insert(self.conn, "task", id=task, request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        ledger.insert(self.conn, "claim", id=cid, task_id=task, kind=kind,
                      question="q", subject_refs=[], checker=checker,
                      origin="derive", created_at="2026")
        return self.conn.execute("SELECT * FROM claim WHERE id=?", (cid,)).fetchone()

    def _said(self, cid, code):
        ledger.append_attempt(
            self.conn, claim_id=cid, subject_digest={}, checker_sha="s",
            config_sha="cfg", head_commit="abc", worktree=".", argv=["x"],
            exit_code=code, stdout="", stderr="", started_at="2026",
            ended_at="2026", duration_ms=1)

    def _state(self, row):
        return state.claim_state(self.conn, self.tmp, row, kinds_cfg=self.cfg.kinds,
                                 config_sha=self.cfg.sha,
                                 checker_sha_of=self.cfg.checker_sha_on_disk)

    def _sign(self, cid, **kw):
        return risk.accept(self.conn, self.cfg, claim_id=cid,
                           kind=kw.pop("kind", "unprovable"), why=self.WHY,
                           require_tty=False, scope=risk.REPO, **kw)

    def test_it_covers_a_later_task_that_the_first_signature_never_saw(self):
        """The whole point: one fact, one signature, however many tasks."""
        r1 = self._claim("t1", "c1")
        self._said("c1", 4)
        self._sign("c1")
        self.assertEqual(self._state(r1), state.RISK_ACCEPTED)

        r2 = self._claim("t2", "c2")
        self.assertEqual(self._state(r2), state.OPEN)   # nothing has run yet
        self._said("c2", 4)
        self.assertEqual(self._state(r2), state.RISK_ACCEPTED)

    def test_it_lapses_the_moment_the_checker_can_answer(self):
        """Add the SPEC.md and the checker speaks again -- nobody has to revoke."""
        r1 = self._claim("t1", "c1")
        self._said("c1", 4)
        self._sign("c1")
        r2 = self._claim("t2", "c2")
        self._said("c2", 1)                             # the file appeared, and it fails
        self.assertEqual(self._state(r2), state.OPEN)

    def test_it_cannot_be_signed_before_the_checker_has_said_so(self):
        """Otherwise `--scope repo` is a hand-operated off switch for any checker."""
        self._claim("t1", "c1")
        with self.assertRaises(risk.RefusedToSign) as caught:
            self._sign("c1")
        self.assertIn("never run", str(caught.exception))

    def test_it_cannot_be_signed_over_a_checker_that_did_answer(self):
        self._claim("t1", "c1")
        self._said("c1", 1)                             # a real violation
        with self.assertRaises(risk.RefusedToSign) as caught:
            self._sign("c1")
        self.assertIn("exit 1", str(caught.exception))

    def test_a_subject_scoped_kind_cannot_be_signed_for_the_repo(self):
        """'This repo structurally cannot answer it' is not true of a kind that
        is raised about particular files."""
        self._claim("t1", "c1", kind="external-write", checker="ext")
        self._said("c1", 4)
        with self.assertRaises(risk.RefusedToSign) as caught:
            self._sign("c1")
        self.assertIn("subject-scoped", str(caught.exception))

    def test_a_risk_about_this_task_s_work_cannot_be_signed_for_the_repo(self):
        self._claim("t1", "c1")
        self._said("c1", 4)
        with self.assertRaises(risk.RefusedToSign) as caught:
            self._sign("c1", kind="baseline_raise")
        self.assertIn("about this task's work", str(caught.exception))

    def test_it_lapses_when_the_checker_itself_changes(self):
        """A different program deserves to be asked again."""
        r1 = self._claim("t1", "c1")
        self._said("c1", 4)
        self._sign("c1")
        self.assertEqual(self._state(r1), state.RISK_ACCEPTED)
        self.assertEqual(
            state.claim_state(self.conn, self.tmp, r1, kinds_cfg=self.cfg.kinds,
                              config_sha=self.cfg.sha,
                              checker_sha_of=lambda cid: "a-different-checker"),
            state.UNSUPPORTED)

    def test_the_record_is_filed_under_the_kind_not_the_task(self):
        """A permanent fact filed under whichever task first tripped over it is
        a fact the next reader cannot find."""
        self._claim("t1", "c1")
        self._said("c1", 4)
        _, path = self._sign("c1")
        self.assertEqual(path.relative_to(self.tmp.resolve()).as_posix(),
                         ".v4/risks/repo/spec-coverage.json")

    def test_it_stops_covering_when_the_checker_starts_erroring(self):
        """A crash is not an absence. Measured in a sandbox: once the repo grew
        the file, the checker exited 5 on something unrelated -- and a signature
        that read 'this cannot be answered here' must not quietly absorb that."""
        self._claim("t1", "c1")
        self._said("c1", 4)
        self._sign("c1")
        r2 = self._claim("t2", "c2")
        self._said("c2", 5)
        self.assertEqual(self._state(r2), state.CHECKER_ERROR)


class RegistrationAsksWhetherThereIsAnythingHere(unittest.TestCase):
    """Fixtures prove a checker works, not that this repo has a subject.

    Reachable before this: `v4 register --id spec-coverage` succeeds in a repo
    with no SPEC.md, `v4 doctor` reports 25 checker(s) match, and the first task
    after that blocks on an exit 4 that nothing predicted.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _checker(self, body):
        p = self.tmp / "c.py"
        p.write_text("import sys\n" + body)
        return p

    def test_it_reports_a_checker_with_nothing_to_read(self):
        c = self._checker("print('no SPEC.md anywhere'); sys.exit(4)\n")
        code, said = register.probe_repo_subject(
            repo_root=self.tmp, checker_path=c, kind="spec-coverage")
        self.assertEqual(code, 4)
        self.assertIn("no SPEC.md", said)

    def test_it_stays_quiet_when_the_checker_can_answer(self):
        c = self._checker("print('scanned 1 file'); sys.exit(0)\n")
        code, _ = register.probe_repo_subject(
            repo_root=self.tmp, checker_path=c, kind="k")
        self.assertEqual(code, 0)

    def test_an_inconclusive_probe_is_not_a_finding(self):
        """A probe that times out has found nothing, and saying otherwise would
        make the warning the thing people learn to ignore."""
        c = self._checker("import time; time.sleep(5)\n")
        code, why = register.probe_repo_subject(
            repo_root=self.tmp, checker_path=c, kind="k", timeout_sec=1)
        self.assertIsNone(code)
        self.assertIn("did not finish", why)


class Composition(unittest.TestCase):
    """Whether the claims can all hold at once.

    Twice the answer was no, and both times it was two mechanisms that were
    individually right: committed bytecode made `test` and `scope` invalidate
    each other, and a signature record did the same to every repo-scoped claim.
    Thirteen agents read the design and found neither, because a collision does
    not live inside any one rule.
    """

    KINDS = {"test": {"staleness": "repo"}, "scope": {"staleness": "repo"},
             "ext": {"staleness": "subject"}}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(self.KINDS))
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        for cid, kind in (("c_test", "test"), ("c_scope", "scope"), ("c_ext", "ext")):
            ledger.insert(self.conn, "claim", id=cid, task_id="t", kind=kind,
                          question="q", subject_refs=[], checker=kind,
                          origin="derive", created_at="2026")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _attempt(self, claim_id, stamp, head="commit-1"):
        """`worktree` is the stamp, because that is the column the rule reads.

        These wrote the stamp into `head_commit` and `"."` into `worktree`,
        which is the criterion the module used to apply and the one its own
        docstring says it does not: "did somebody commit between these two
        answers" is the ordinary flow, and "did answering one move the content
        the other is judged by" is the question.
        """
        ledger.append_attempt(
            self.conn, claim_id=claim_id, subject_digest={}, checker_sha="s",
            config_sha="c", head_commit=head, worktree=stamp, argv=["x"],
            exit_code=0, stdout="", stderr="", started_at="2026", ended_at="2026",
            duration_ms=1)

    def _report(self):
        return composition.report(self.conn, self.tmp, "t")

    def test_same_stamp_is_no_collision(self):
        self._attempt("c_test", "tree-A")
        self._attempt("c_scope", "tree-A")
        ok, lines = self._report()
        self.assertTrue(ok, lines)

    def test_one_answer_moving_the_tree_is_reported(self):
        self._attempt("c_scope", "tree-A")
        self._attempt("c_test", "tree-B")
        ok, lines = self._report()
        self.assertFalse(ok)
        self.assertIn("c_scope", " ".join(lines))

    def test_each_answering_twice_against_different_trees_is_a_livelock(self):
        """The shape that made shipping impossible: neither can be the last word."""
        for stamp, cid in (("A", "c_scope"), ("B", "c_test"),
                           ("C", "c_scope"), ("D", "c_test")):
            self._attempt(cid, f"tree-{stamp}")
        ok, lines = self._report()
        self.assertFalse(ok)
        self.assertIn("LIVELOCK", " ".join(lines))

    def test_a_commit_between_two_answers_is_not_a_collision(self):
        """The ordinary flow: the tree the checkers saw never changed."""
        self._attempt("c_scope", "tree-A", head="commit-1")
        self._attempt("c_test", "tree-A", head="commit-2")
        ok, lines = self._report()
        self.assertTrue(ok, lines)

    def test_subject_scoped_claims_are_not_compared(self):
        """They are judged on their own files, so another claim cannot move them."""
        self._attempt("c_ext", "tree-A")
        self._attempt("c_test", "tree-B")
        ok, _ = self._report()
        self.assertTrue(ok)

    def test_a_single_repo_scoped_kind_cannot_collide_with_itself(self):
        (self.tmp / ".v4" / "claim_kinds.json").write_text(
            json.dumps({"test": {"staleness": "repo"}}))
        self._attempt("c_test", "tree-A")
        ok, lines = self._report()
        self.assertTrue(ok)
        self.assertIn("nothing can collide", lines[0])


class OrphanClaims(unittest.TestCase):
    """`git mv` on an in-scope file used to hold a task forever.

    The claim's subject stopped existing, its checker reported it could not
    verify, and UNSUPPORTED is deliberately not terminal -- so nothing cleared
    it and no command existed to. Renaming a file is not a risk to sign for.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"probe": {"question_template": "q {file}", "checker": "probe",
                       "staleness": "subject"}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "app.py").write_text("x = 1\n")
        self.det = self.tmp / "detectors"
        self.det.mkdir()
        self._detector(fires=True)
        self.cfg = config.RepoConfig(self.tmp)
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _detector(self, *, fires=True, crashes=False):
        body = ("import argparse,sys\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--subject');p.add_argument('--facts');p.add_argument('--out')\n"
                "p.parse_args()\n")
        if crashes:
            body += "sys.exit(3)\n"
        elif fires:
            body += "print('V4-CLAIM: kind=probe file=app.py symbol=f')\nsys.exit(0)\n"
        else:
            body += "sys.exit(0)\n"
        (self.det / "probe.py").write_text(body)
        self._register_probe()

    def _register_probe(self):
        """What `v4 register-detector` writes, and why every rewrite repeats it.

        `derive` refuses a detector whose bytes are not the registered ones, so
        editing one between derivations without re-registering is the same
        situation as editing a checker: the gate applies to the file that runs.
        """
        reg = {"probe.py": {"path": "detectors/probe.py",
                            "sha256": hashing.file_sha(self.det / "probe.py"),
                            "fixtures": "tests/fixtures/probe_detector",
                            "cases": 6}}
        (self.tmp / ".v4" / "detectors.json").write_text(json.dumps(reg))
        if getattr(self, "cfg", None) is not None:
            self.cfg.detectors = reg

    def _derive(self):
        return derive.derive(self.conn, self.cfg, task_id="t", scope_globs=["**"],
                             subject_files=["app.py"], phase="open",
                             detectors_dir=self.det)

    def _states(self):
        rows = state.task_claims(self.conn, "t")
        return [state.claim_state(self.conn, self.tmp, r, kinds_cfg=self.cfg.kinds,
                                  config_sha=self.cfg.sha,
                                  checker_sha_of=lambda _: "") for r in rows]

    def test_a_renamed_away_file_retracts_its_claim(self):
        self._derive()
        self.assertEqual(len(state.task_claims(self.conn, "t")), 1)
        (self.tmp / "app.py").unlink()
        self._detector(fires=False)
        res = self._derive()
        self.assertEqual(len(res["retracted"]), 1)
        self.assertEqual(self._states(), [state.RETRACTED])
        self.assertIn(state.RETRACTED, state.TERMINAL)

    def test_a_crashed_detector_retracts_nothing(self):
        """Silence from a broken detector is not evidence that nothing applies."""
        self._derive()
        (self.tmp / "app.py").unlink()
        self._detector(crashes=True)
        res = self._derive()
        self.assertEqual(res["retracted"], [])
        self.assertNotEqual(self._states(), [state.RETRACTED])

    def test_a_narrowed_rule_stops_holding_what_it_already_passed(self):
        """The second way a claim stops applying, and the one with no exit.

        A detector repaired for a false positive stops raising claims it used
        to raise. Those keep the sha of the version that raised them, which
        `state` compares against the registry -- so they read STALE forever:
        the re-derive does not raise them, the row cannot be updated, and
        answering them again changes nothing. Measured on the task that found
        it: 14 `fail-closed` claims, every one already PASS.
        """
        res = self._derive_with(emit=False, answered=0)
        self.assertEqual([r[0] for r in res["retracted"]], ["c-probe"])
        self.assertIn("no longer does", res["retracted"][0][3])

    def test_but_one_that_failed_still_holds_the_task(self):
        """Otherwise editing a detector until it stops raising something would
        be a way to make a finding go away."""
        res = self._derive_with(emit=False, answered=1)
        self.assertEqual(res["retracted"], [])

    def test_and_neither_does_one_nobody_has_answered(self):
        res = self._derive_with(emit=False, answered=None)
        self.assertEqual(res["retracted"], [])

    def _derive_with(self, *, emit: bool, answered):
        """A task holding one claim, and a detector that may or may not raise it."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({"probe": {
            "question_template": "does this hold?", "checker": "probe",
            "detector": "always_probe.py", "staleness": "subject"}}))
        (tmp / ".v4" / "checkers.json").write_text(json.dumps(
            {"probe": {"path": "checkers/probe.py", "kinds": ["probe"]}}))
        (tmp / "detectors").mkdir()
        line = ("print('V4-CLAIM: kind=probe file=app.py symbol=go')\n"
                if emit else "pass\n")
        (tmp / "detectors" / "always_probe.py").write_text(line)
        (tmp / "app.py").write_text("def go():\n    return 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)

        conn, cfg = ledger.connect(tmp), config.RepoConfig(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-20T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c-probe", task_id="t1", kind="probe",
                      question="q",
                      subject_refs=[{"kind": "file", "path": "app.py"}],
                      checker="probe", origin="derive", file="app.py",
                      symbol="go", variant=None, line=None, note=None,
                      detector="always_probe.py", detector_sha="an-older-one",
                      created_at="2026-08-20T00:00:00+00:00")
        if answered is not None:
            ledger.append_attempt(
                conn, claim_id="c-probe", subject_digest="d", checker_sha="s",
                config_sha="c", head_commit="h", worktree=str(tmp), facts_sha="",
                argv=["x"], exit_code=answered, stdout="", stderr="",
                duration_ms=1, started_at="2026-08-20T00:00:01+00:00",
                ended_at="2026-08-20T00:00:01+00:00")
        return derive.derive(conn, cfg, task_id="t1", scope_globs=["**"],
                                 subject_files=["app.py"], phase="open",
                                 detectors_dir=tmp / "detectors")

    def test_a_claim_whose_file_still_exists_is_not_retracted(self):
        """A narrowed rule does not erase the code it stopped judging."""
        self._derive()
        self._detector(fires=False)
        res = self._derive()
        self.assertEqual(res["retracted"], [])


class Engagement(unittest.TestCase):
    """Mechanical only, and on purpose.

    The failure it answers -- knowing a rule and doing it anyway -- is real but
    unmeasured here, so the layer is built to cost almost nothing and to be
    removable. A subjective judge would be worse than none: it cannot be
    satisfied deliberately, so it has no bound, which is the loop this design
    exists to remove.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"ext": {"question_template": "Does {symbol} confirm its write?",
                     "checker": "ext", "staleness": "subject", "engagement": True}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        self.cfg = config.RepoConfig(self.tmp)
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        ledger.insert(self.conn, "claim", id="c", task_id="t", kind="ext",
                      question="Does notify confirm its write?", subject_refs=[],
                      checker="ext", origin="derive", file="app/chat/notifier.py",
                      symbol="notify", created_at="2026")
        self.row = self.conn.execute("SELECT * FROM claim WHERE id='c'").fetchone()

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _judge(self, text, **kw):
        return engagement.judge(self.conn, self.cfg, claim_row=self.row,
                                sentence=text, **kw)

    GOOD = ("notify sends into the chat platform, and the brand name lands in the outbound "
            "message body where chat_id must never appear beside it")

    def test_a_repo_scoped_claim_can_be_engaged_with_at_all(self):
        """`<module>` is a placeholder, not a name anybody can write.

        Measured on a live repo: `dep-provenance` is repo-scoped, so it carries
        no file and its symbol is `<module>`. That made `{"<module>"}` the only
        thing a sentence could name, the tokeniser strips angle brackets, and
        three sentences that each named the real file were refused with "name
        the file or the symbol". The kind was unengageable, so the claim was
        unanswerable, so the task could not ship.
        """
        ledger.insert(self.conn, "claim", id="repo1", task_id="t", kind="ext",
                      question="Has anything resolved every package?",
                      subject_refs=[], checker="ext", origin="derive",
                      file="", symbol="<module>", created_at="2026")
        row = self.conn.execute("SELECT * FROM claim WHERE id='repo1'").fetchone()
        ok, why = engagement.judge(
            self.conn, self.cfg, claim_row=row,
            sentence="this repo ships no lockfile at all, so the version of "
                     "requests that app/chat/client.py imports has never "
                     "been confirmed against a registry by anything")
        self.assertTrue(ok, why)

    def test_empty_is_refused(self):
        self.assertFalse(self._judge("")[0])

    def test_too_short_is_refused(self):
        self.assertFalse(self._judge("read it")[0])

    def test_restating_the_question_is_refused(self):
        ok, why = self._judge("Does notify confirm its write?")
        self.assertFalse(ok)

    def test_saying_nothing_about_the_subject_is_refused(self):
        ok, why = self._judge(
            "I have considered the situation carefully and am confident it is fine.")
        self.assertFalse(ok)
        self.assertIn("name the file or the symbol", why)

    def test_a_sentence_about_the_subject_is_accepted(self):
        ok, why = self._judge(self.GOOD)
        self.assertTrue(ok, why)

    def test_duplicates_are_compared_across_every_task_not_just_this_one(self):
        """V3 died of one sentence appearing 38,392 times, across tasks.

        Across *claims*: the sentence recorded here belongs to a different
        finding in another task, which is the shape that failure had. The same
        worker answering the same finding again is the opposite case and is
        allowed -- see `TheSameClaimMetTwice`.
        """
        ledger.insert(self.conn, "task", id="other-task", request="r",
                      scope_globs=["**"], base_commit="x", created_at="2026")
        ledger.insert(self.conn, "claim", id="c-elsewhere", task_id="other-task",
                      kind="ext", question="q", subject_refs=[], checker="ext",
                      origin="derive", file="app/other.py", symbol="different",
                      created_at="2026")
        engagement.record(self.conn, task_id="other-task", claim_id="c-elsewhere",
                          sentence=self.GOOD, verdict="accepted", reason="")
        ok2, why = self._judge(self.GOOD)
        self.assertFalse(ok2)
        self.assertIn("spans every", why)

    def test_a_second_widen_is_exempt_from_duplicate_detection(self):
        """Two widens in one task have the same reason; refusing the second
        makes the cheap exit expensive, which is how it stops being used."""
        engagement.record(self.conn, task_id="t", claim_id="c", sentence=self.GOOD,
                          verdict="accepted", reason="")
        self.assertTrue(self._judge(self.GOOD, exempt_duplicate=True)[0])

    def test_only_an_accepted_sentence_unblocks_a_claim(self):
        engagement.record(self.conn, task_id="t", claim_id="c", sentence="no",
                          verdict="rejected_machine", reason="too short")
        self.assertIsNone(engagement.accepted_for(self.conn, "c"))
        engagement.record(self.conn, task_id="t", claim_id="c", sentence=self.GOOD,
                          verdict="accepted", reason="")
        self.assertEqual(engagement.accepted_for(self.conn, "c"), self.GOOD)

    def test_a_kind_that_does_not_ask_for_one_is_not_blocked(self):
        row = dict(self.row); row["kind"] = "other"
        self.assertFalse(engagement.required_for(self.cfg, row))


class KernelDoesNotMoveTheTreeItJudges(unittest.TestCase):
    """One invariant, three bugs before it was written down.

    A signature record, the chain anchor and the checker registry are all
    kernel output landing under .v4/. Each one, counted in the working-tree
    digest, made recording an answer invalidate every repo-scoped claim
    including the one just answered. The third arrived the same day the first
    two were fixed, which is why this is an enumerated list with a test rather
    than a habit.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=self.tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=self.tmp, check=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel, body="{}"):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)

    def test_every_kernel_written_path_leaves_the_digest_alone(self):
        before = hashing.worktree_digest(self.tmp)
        # A trailing slash is a directory, so what gets written is a file inside
        # it. Naming one such file here instead meant the set could grow a
        # second directory and this test would still only cover the first.
        for entry in sorted(hashing.KERNEL_WRITTEN):
            rel = entry + "abc.json" if entry.endswith("/") else entry
            self._write(rel)
            self.assertEqual(
                hashing.worktree_digest(self.tmp), before,
                f"writing {rel} moved the digest; recording an answer would "
                f"invalidate every repo-scoped claim including that one")

    def test_human_edited_config_still_counts(self):
        """config.json is the test claim's oracle -- changing it must be seen."""
        before = hashing.worktree_digest(self.tmp)
        self._write(".v4/config.json", '{"test_command": "pytest -k nothing"}')
        self.assertNotEqual(hashing.worktree_digest(self.tmp), before)

    def test_claim_kinds_still_counts(self):
        before = hashing.worktree_digest(self.tmp)
        self._write(".v4/claim_kinds.json", '{"test": {}}')
        self.assertNotEqual(hashing.worktree_digest(self.tmp), before)

    def test_ordinary_work_still_counts(self):
        before = hashing.worktree_digest(self.tmp)
        (self.tmp / "app.py").write_text("x = 2\n")
        self.assertNotEqual(hashing.worktree_digest(self.tmp), before)


class ReadsFactsFollowsImports(unittest.TestCase):
    """The narrowing guard has to see every detector a table can turn off.

    `_reads_facts` decides which detectors `derive` runs twice. It used to scan
    the detector's own text, and of the three detectors that consume the facts
    table only one reads it in its own file -- so the guard against a table
    silently disabling a detector was not watching two of the three.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def _conditional(self):
        return {p.name for p in sorted((self.ROOT / "detectors").glob("*.py"))
                if not p.name.startswith("_") and derive.reads_facts(p)}

    #: Ground truth by inspection, not by the function under test: a detector
    #: consumes the table when it or something it imports reads the dict.
    #: `dal_write` and `webhook_replay` joined the day their checkers stopped
    #: being unreachable -- both were written, registered and named by a claim
    #: kind with no detector emitting it, so neither had ever run.
    #: `surface_proof` and `runtime_proof` joined when their raise condition
    #: became a fact the repo declares about itself -- `ui_globs` and
    #: `outbound_write` -- rather than a command it happened to have written.
    #: That is what makes the forcing function stack-neutral: a Go tree declares
    #: the same two facts a Node one does.
    #: Four left when their kinds were cut -- `bundle_secret`, `route_auth`,
    #: `dal_write` and `webhook_replay`. Each read the table and each was
    #: gated by a facts key nothing ever filled, which is why they raised
    #: 665, 703-in-one-task, 0 and 1 claims respectively across 113 tasks.
    READS_THE_TABLE = {"external_write.py",
                       "surface_proof.py", "runtime_proof.py"}

    def test_the_detectors_that_read_the_table_are_all_seen(self):
        self.assertEqual(self._conditional(), self.READS_THE_TABLE)

    def test_reaching_the_table_through_an_import_counts(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "kernel" / "analysis").mkdir(parents=True)
            (root / "detectors").mkdir()
            (root / "kernel" / "__init__.py").write_text("")
            (root / "kernel" / "analysis" / "__init__.py").write_text("")
            (root / "kernel" / "analysis" / "thing.py").write_text(
                "def scan(root, facts):\n    return facts.get('ui_globs') or []\n")
            det = root / "detectors" / "thing.py"
            det.write_text(
                "from kernel.analysis import thing\n"
                "p.add_argument('--facts')\n")
            self.assertTrue(derive.reads_facts(det, root))

    def test_declaring_the_flag_is_not_reading_the_table(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "detectors").mkdir()
            det = root / "detectors" / "plain.py"
            det.write_text("p.add_argument('--facts')\nprint('V4-CLAIM: kind=x')\n")
            self.assertFalse(derive.reads_facts(det, root))


class StopGateAsksOnce(unittest.TestCase):
    """Blocking the same stop twice is a loop, not twice the friction.

    The hook offers three endings and one of them -- record the FAIL and say so
    -- produces the identical payload on the next stop. Without reading
    `stop_hook_active` the worker gets the identical message forever, and the
    only reachable ending is the one the message does not list.
    """

    HOOK = REPO / "hooks" / "stop_gate.py"

    def _run(self, payload):
        return subprocess.run([sys.executable, str(self.HOOK)],
                              input=json.dumps(payload), text=True,
                              capture_output=True, cwd=REPO)

    def test_a_repeat_stop_is_let_through(self):
        r = self._run({"stop_hook_active": True, "session_id": "s"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout or "{}"), {})

    def test_a_crash_never_becomes_a_block(self):
        # A hook that blocks every turn is a hook somebody switches off.
        r = subprocess.run([sys.executable, str(self.HOOK)], input="not json",
                           text=True, capture_output=True, cwd=REPO)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("block", r.stdout)


class WhichFactsTableIsThisRepos(unittest.TestCase):
    """`_load_facts` prefers `.v4/facts.<repo>.json` and falls back to the first.

    The fallback is the branch that matters and it had no test. Two checkers
    spell the same resolution as `sorted(glob(".v4/facts*.json"))[0]` with no
    preference at all, so a repo carrying a second table -- which this one did,
    an adopter's beside `facts.vibeproof.json` -- can be judged
    against another project's vocabulary, and this repo escaped it only because
    its own name sorts first. Pinning the owner is what lets those two be
    pointed at it.
    """

    #: A table `facts_grammar.validate` accepts; `_load_facts` raises on one it
    #: does not, so an under-filled table would fail these for the wrong reason.
    TABLE = {
        "repo": "demo", "generated_from_commit": "0" * 40,
        "outbound_write": [{"pattern": "requests.post",
                            "seen_at": "a/b.py:3", "kind": "http"}],
        "outbound_read": [{"pattern": "requests.get",
                           "seen_at": "a/b.py:9", "kind": "http"}],
        "auth_decision": [{"pattern": "check_permission",
                           "seen_at": "a/auth.py:1", "kind": "authz"}],
        "entrypoint_globs": ["workers/*.py"], "ui_globs": ["ui/src/**"],
        "config_files": ["requirements.txt"], "protected_paths": [".v4/**"],
    }

    def _table(self, name):
        return json.dumps(dict(self.TABLE, repo=name))

    def _repo(self, *table_names):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / ".v4" / "checkers.json").write_text("{}")
        for name in table_names:
            (tmp / ".v4" / name).write_text(self._table(name))
        return tmp

    def test_the_repos_own_table_wins_over_one_that_sorts_earlier(self):
        tmp = self._repo("facts.aaa.json")
        (tmp / ".v4" / f"facts.{tmp.name}.json").write_text(self._table("mine"))
        cfg = config.RepoConfig(tmp)
        self.assertEqual(cfg.facts_path.name, f"facts.{tmp.name}.json")
        self.assertEqual(cfg.facts["repo"], "mine",
                         "the path decides which vocabulary is loaded")

    def test_with_no_named_table_the_only_one_is_read(self):
        """One table and no preferred name is not a guess."""
        tmp = self._repo("facts.aaa.json")
        self.assertEqual(config.RepoConfig(tmp).facts_path.name,
                         "facts.aaa.json")

    def test_with_no_named_table_and_two_of_them_it_refuses_to_pick(self):
        """This expectation used to be `facts.aaa.json`, and that was wrong.

        Between two tables and no name to prefer, "the first" is alphabetical
        order and not ownership -- the class docstring above says so itself,
        that a repo carrying a second table "can be judged against another
        project's vocabulary", and adds that this repo escaped it only because
        its own name sorted first. It stopped escaping: the adopter's table
        sorted before `facts.vibeproof.json`, and in a checkout named anything
        but `vibeproof` -- which is every clone into a directory of the
        cloner's choosing -- the adopter's table was read as this repo's.
        The table has since moved out of `.v4/`, so this repo answers under any
        directory name; the case below still asks the question, on a tree it
        builds itself.
        Measured in a tree called `differently-named`: the auth-decision scan
        found none of this repo's own hooks, and said nothing about it.

        `None` is the honest answer. It reaches `v4 doctor` as a table nothing
        can find; the old answer reached every conditional detector as a
        vocabulary that quietly did not describe the repo it was judging.
        """
        tmp = self._repo("facts.aaa.json", "facts.zzz.json")
        cfg = config.RepoConfig(tmp)
        self.assertIsNone(cfg.facts_path)
        self.assertEqual(cfg.facts, {})

    def test_no_table_at_all_is_no_path_and_no_facts(self):
        tmp = self._repo()
        cfg = config.RepoConfig(tmp)
        self.assertIsNone(cfg.facts_path)
        self.assertEqual(cfg.facts, {})


class PolicyIsRead(unittest.TestCase):
    """`policy` decided nothing for as long as it was a required field.

    SPEC.md §6 offers a repo two policies and says what each buys. Nothing read
    the value: `no_accepted_risk` behaved exactly like `allow_accepted_risk`, so
    a repo that meant to switch signing off got signing, and the only way to
    find out was to sign something and watch it work.
    """

    def _repo(self, policy):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps({
            "test_command": "true", "policy": policy,
            "thresholds": {"min_chars": 40}}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({
            "test": {"question_template": "q", "checker": "test",
                     "staleness": "repo"}}))
        (tmp / ".v4" / "checkers.json").write_text("{}")
        (tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, check=True)
        return tmp

    WHY = "there is no oracle for this and the reason runs past forty characters"

    def test_no_accepted_risk_refuses_to_sign(self):
        tmp = self._repo("no_accepted_risk")
        cfg = config.RepoConfig(tmp)
        conn = ledger.connect(tmp)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        ledger.insert(conn, "claim", id="c", task_id="t", kind="test", question="q",
                      subject_refs=[], checker="test", origin="derive",
                      created_at="2026")
        with self.assertRaises(risk.RefusedToSign) as caught:
            risk.accept(conn, cfg, claim_id="c", kind="unprovable",
                        why=self.WHY, require_tty=False)
        self.assertIn("no_accepted_risk", str(caught.exception))

    def test_a_policy_nobody_recognises_is_refused_rather_than_defaulted(self):
        # A misspelling used to land on the permissive side in silence.
        tmp = self._repo("allow-accepted-risk")
        with self.assertRaises(config.ConfigError) as caught:
            config.RepoConfig(tmp)
        self.assertIn("allow-accepted-risk", str(caught.exception))

    def _with_threshold(self, name, value):
        tmp = self._repo("allow_accepted_risk")
        cfg = json.loads((tmp / ".v4" / "config.json").read_text())
        cfg["thresholds"][name] = value
        (tmp / ".v4" / "config.json").write_text(json.dumps(cfg))
        return tmp

    def test_an_unusable_threshold_is_refused_at_load(self):
        """These four numbers gate shipping, widening and signing.

        `policy` and `staleness` are both validated by value here with written
        reasons, and `thresholds` was merged raw. A string or a null then raised
        TypeError from inside `scope.widen` or `risk.accept` -- at the moment
        somebody was trying to get past the gate, with a message about `<` and
        `str`. An unusable threshold is a config error and belongs where the
        file is read.
        """
        for name, value in (("min_chars", None), ("min_chars", "40"),
                            ("min_chars", True), ("ship_rederive_max", "3"),
                            ("dup_threshold", "0.8"), ("widen_warn_pct", None)):
            with self.subTest(name=name, value=value):
                with self.assertRaises(config.ConfigError) as caught:
                    config.RepoConfig(self._with_threshold(name, value))
                self.assertIn(name, str(caught.exception))

    def test_a_negative_floor_is_refused(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.RepoConfig(self._with_threshold("min_chars", -1))
        self.assertIn("negative", str(caught.exception))

    def test_a_float_percentage_is_fine(self):
        """Refusing has to be narrow: `widen_warn_pct` is a percentage."""
        cfg = config.RepoConfig(self._with_threshold("widen_warn_pct", 2.5))
        self.assertEqual(cfg.thresholds["widen_warn_pct"], 2.5)


class DetectorGateIsEnforced(unittest.TestCase):
    """Passing a gate has to leave a trace, or nothing can require it.

    `v4 verify-detector` existed and printed a verdict that went nowhere, so the
    spec's requirement that a conditional detector pass it had nothing behind
    it: derive ran every file in `detectors/`, `registry-consistency` compared
    names and not hashes, and `detector_sha` was taken at derivation time from
    the file about to run -- nothing to compare it against.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"probe": {"question_template": "q {file}", "checker": "probe",
                       "staleness": "subject"}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        (self.tmp / "app.py").write_text("x = 1\n")
        self.det = self.tmp / "detectors"
        self.det.mkdir()
        self.conn = ledger.connect(self.tmp)
        self.addCleanup(self.conn.close)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")

    def _write(self, name):
        body = ("import argparse,sys\n"
                "p=argparse.ArgumentParser()\n"
                "p.add_argument('--subject');p.add_argument('--facts');"
                "p.add_argument('--out')\n"
                "p.parse_args()\n"
                "print('V4-CLAIM: kind=probe file=app.py symbol=f')\nsys.exit(0)\n")
        (self.det / name).write_text(body)

    def _register(self, name):
        reg = json.loads((self.tmp / ".v4" / "detectors.json").read_text()) \
            if (self.tmp / ".v4" / "detectors.json").is_file() else {}
        reg[name] = {"path": f"detectors/{name}",
                     "sha256": hashing.file_sha(self.det / name),
                     "fixtures": "x", "cases": 6}
        (self.tmp / ".v4" / "detectors.json").write_text(json.dumps(reg))

    def _derive(self):
        cfg = config.RepoConfig(self.tmp)
        return derive.derive(self.conn, cfg, task_id="t", scope_globs=["**"],
                             subject_files=["app.py"], phase="open",
                             detectors_dir=self.det)

    def test_an_unregistered_conditional_detector_does_not_run(self):
        self._write("probe.py")
        res = self._derive()
        self.assertEqual(res["created"], [])
        self.assertTrue(any(r[1] == "unregistered" for r in res["refused"]),
                        res["refused"])

    def test_a_registered_one_runs(self):
        self._write("probe.py")
        self._register("probe.py")
        res = self._derive()
        self.assertEqual(len(res["created"]), 1, res)

    def test_editing_a_registered_detector_stops_it(self):
        # The same rule a checker lives under: the bytes that run are the bytes
        # that were gated. A detector chooses which claims exist at all, so the
        # silent version of this failure is worse than a checker's.
        self._write("probe.py")
        self._register("probe.py")
        (self.det / "probe.py").write_text(
            (self.det / "probe.py").read_text() + "\n# edited\n")
        res = self._derive()
        self.assertEqual(res["created"], [])
        self.assertTrue(any(r[1] == "detector-changed" for r in res["refused"]),
                        res["refused"])

    def test_an_unconditional_detector_needs_no_registration(self):
        # SPEC.md §2: it emits one fixed line and does not read the tree, so
        # "should not fire" cannot be written as a fixture. Its kind's checker
        # is what gates it.
        self._write("always_probe.py")
        res = self._derive()
        self.assertEqual(len(res["created"]), 1, res)


class DetectorFixturesAreNotBorrowed(unittest.TestCase):
    """A checker's green case can be a detector's red one.

    `external-write` is the case: the detector asks whether a change touches
    client source and the checker asks whether a secret is reachable from the
    bundle, so every one of the checker's green cases -- front-end files with no
    leak -- is a file the detector must fire on. Four of its five passed anyway,
    because their paths resolved outside `ui_globs`.
    """

    def test_a_shared_analysis_means_they_cannot_disagree(self):
        # external_write's detector and checker both call
        # kernel.analysis.external_write and say so, so one fixture set is the
        # guarantee rather than a shortcut.
        self.assertEqual(
            register._borrowed_from_a_checker(
                REPO, REPO / "tests/fixtures/external_write",
                REPO / "detectors/external_write.py"),
            "")

    def test_no_shared_analysis_is_refused(self):
        problem = register._borrowed_from_a_checker(
            REPO, REPO / "tests/fixtures/test_token_shape",
            REPO / "detectors/test_token_shape.py")
        self.assertIn("test_token_shape_detector", problem)

    def test_the_judgement_may_carry_a_suffix(self):
        """`test_expectation`'s two halves share `test_expectation_diff`.

        An exact stem match refused a pair that cannot disagree, and refusing
        registration is not a small thing: `derive` skips an unregistered
        detector, so `test-expectation` -- the rule that catches an expectation
        edited to match the result -- stops being raised at all.
        """
        self.assertTrue(register._shares_logic(
            REPO, REPO / "detectors/test_expectation.py",
            "checkers/test_expectation.py"))
        self.assertEqual(register._borrowed_from_a_checker(
            REPO, REPO / "tests/fixtures/test_expectation",
            REPO / "detectors/test_expectation.py"), "")

    def test_sharing_the_plumbing_is_not_sharing_the_judgement(self):
        """Two halves reading one vocabulary module is plumbing, not judgement.

        `bundle_secret` was the real pair that showed this -- both halves read
        `ui_globs` from `facts_grammar` -- and it was cut. Nothing registered
        imports `facts_grammar` in both halves any more, so the case is built
        here rather than borrowed: a rule module named after the rule is
        judgement, any other shared import is not, and that distinction does
        not depend on which kinds happen to exist.

        The original wording follows, because the reason has not changed.

        `_shares_logic` asked whether they import any `kernel.analysis.*` module
        in common, and the plumbing -- `subject_files`, `pysource`,
        `facts_grammar` -- is imported by half the repo. So the moment one rule
        was given one implementation, this function started reporting that a
        detector and a checker which can still disagree about a leak cannot.
        The module that holds a rule is named after the rule.
        """
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "detectors").mkdir()
        (tmp / "checkers").mkdir()
        plumbing = "from kernel.analysis import facts_grammar\n"
        (tmp / "detectors" / "widget.py").write_text(plumbing)
        (tmp / "checkers" / "widget.py").write_text(plumbing)
        self.assertFalse(register._shares_logic(
            tmp, tmp / "detectors/widget.py", "checkers/widget.py"),
            "a shared vocabulary module is not a shared rule")
        rule = "from kernel.analysis import widget\n"
        (tmp / "detectors" / "widget.py").write_text(rule)
        (tmp / "checkers" / "widget.py").write_text(rule)
        self.assertTrue(register._shares_logic(
            tmp, tmp / "detectors/widget.py", "checkers/widget.py"),
            "the module named after the rule is the judgement")
        # Identity, not spelling: the point is that both halves reach *the
        # same* module object, and an import line asserted as text is satisfied
        # by a file that imports it and then rebinds the name.
        #
        # `kernel.analysis.external_write`, not `facts_grammar`. These four
        # lines sat behind a bare `return` -- the only unreachable block in the
        # suite -- and when the return came out they failed: neither file binds
        # `facts_grammar` any more. Both bind `external_write as analysis`, so
        # that is the shared owner now, and asking about the old name was
        # asking about nothing. The question is the one the comment always
        # stated; the name it is asked of moved.
        from kernel.analysis import external_write as owner
        for rel in ("detectors/external_write.py", "checkers/external_write.py"):
            mod = _load_module(REPO / rel)
            self.assertIs(mod.analysis, owner, rel)

    def test_its_own_set_is_always_fine(self):
        self.assertEqual(
            register._borrowed_from_a_checker(
                REPO, REPO / "tests/fixtures/external_write_detector",
                REPO / "detectors/external_write.py"),
            "")


class ClaimLinesHaveAReader(unittest.TestCase):
    """`V4-CLAIM:` written where nothing parses it.

    `.claude/agents/reviewer.md` told the reviewer to print those lines for as
    long as it existed. `parse_claim_lines` is called from two places -- a
    detector's stdout and a fixture run -- so every finding a reviewer produced
    went into its own transcript and stopped there, while the entry point that
    works (`v4 review add`) went unmentioned.
    """

    def _dw(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dw_probe", REPO / "checkers" / "dead_wiring.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _probe(self, body, name="r.md"):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".claude" / "agents").mkdir(parents=True)
        (tmp / ".claude" / "agents" / name).write_text(body)
        return self._dw()._claim_lines_have_a_reader(tmp)

    def test_a_prompt_that_emits_one_is_reported(self):
        self.assertTrue(self._probe(
            "出 claim:\n\n```\nV4-CLAIM: kind=review-finding file=a.py\n```\n"))

    def test_prose_naming_it_is_not_emitting_it(self):
        # The paragraph telling a reviewer not to print the line must not be
        # the thing that trips the rule -- which is the bug `dead_wiring` first
        # found on itself, in a comment explaining a key it was meant to find.
        self.assertFalse(self._probe(
            "唔好印 `V4-CLAIM:` 行 —— 冇任何嘢讀佢哋。\n"))

    def test_this_repo_is_clean(self):
        self.assertEqual(self._dw()._claim_lines_have_a_reader(REPO), [])

    def test_the_reviewer_uses_the_entry_point_that_exists(self):
        import shlex
        text = (REPO / ".claude" / "agents" / "reviewer.md").read_text()
        commands = [shlex.split(line) for line in text.replace("\\\n", " ").splitlines()
                    if line.startswith("./bin/v4") and "review add" in line]
        # An explicit worktree path uses the same entry point as --repo .
        # Executable examples additionally prove task ownership in
        # test_reviewer_examples_preserve_task.py.
        self.assertTrue(any(args[:2] == ["./bin/v4", "--repo"] and args[2]
                            and args[3:5] == ["review", "add"] for args in commands))


class AFindingIsRaisedWithCoordinatesThatCanClose(unittest.TestCase):
    """`redgreen` matches `code.co_name`, so a dotted symbol never closes.

    Measured on one eight-task run: 71 signatures, of which about 52 said some
    version of "my own malformed coordinates" -- the worker filed the symbol as
    the reviewer wrote it, `ChatBotClient._request`, and the tracer compares
    the bare name a frame carries. One of them noted it was the second time the
    repo had paid for the same mistake. The claim could never close, so the only
    exit left was the human touchpoint the design says must stay rare.
    """

    SRC = ('"""A wrapper."""\n\n\nclass Client:\n    def fetch(self, url):\n'
           '        return url\n\n\nTIMEOUTS = {"read": 30}\n')

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / "app").mkdir()
        (self.tmp / "app" / "svc.py").write_text(self.SRC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _r(self, symbol):
        return review.resolve_symbol(self.tmp, "app/svc.py", symbol)

    def test_a_dotted_symbol_becomes_the_name_a_frame_carries(self):
        self.assertEqual(self._r("Client.fetch"), "fetch")

    def test_a_bare_symbol_is_left_alone(self):
        self.assertEqual(self._r("fetch"), "fetch")

    def test_a_value_is_refused_because_no_frame_is_ever_named_after_one(self):
        with self.assertRaises(review.BadCoordinates) as c:
            self._r("TIMEOUTS")
        self.assertIn("not something that runs", str(c.exception))

    def test_a_symbol_the_file_does_not_have_is_refused(self):
        with self.assertRaises(review.BadCoordinates) as c:
            self._r("Client.nope")
        self.assertIn("defines no", str(c.exception))

    def test_a_symbol_on_a_non_python_subject_is_refused(self):
        """This asserted the opposite -- that any string is accepted -- which is
        the state `.github/monitor/PROMPT.md` describes as impossible ("refused
        at this command rather than four hours later") and which put three
        unclosable claims in this ledger, two of them naming functions that live
        in `.py` files somewhere else entirely."""
        (self.tmp / "README.md").write_text("x")
        with self.assertRaises(review.BadCoordinates) as caught:
            review.resolve_symbol(self.tmp, "README.md", "whatever")
        self.assertIn("text closure", str(caught.exception),
                      "the refusal has to name the route that does exist")

    def test_a_non_python_subject_with_no_symbol_is_fine(self):
        """A finding *about* a document is legitimate. It closes by text
        closure, which needs no frame -- so the symbol is what is refused, not
        the finding."""
        (self.tmp / "README.md").write_text("x")
        self.assertEqual(review.resolve_symbol(self.tmp, "README.md", ""), "")

    def test_a_file_that_is_not_here_is_refused(self):
        with self.assertRaises(review.BadCoordinates):
            review.resolve_symbol(self.tmp, "no/such.py", "f")


class AFindingWhoseRepairIsASentence(unittest.TestCase):
    """Thirteen of those 71 signatures were right that no test could close them.

    "The repair for this finding is a docstring and nothing else, so there is no
    test that can be red at the parent." Red-green over the text instead: the
    marker was in the file at the parent and is not now. It proves a string
    moved and says only that.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        ledger.insert(self.conn, "claim", id="c", task_id="t",
                      kind="review-finding", question="q", subject_refs=[],
                      checker="review-finding", origin="review", created_at="2026")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    LONG = "Minimal HTTP wrapper around the service API."

    def test_it_records_what_has_to_have_moved(self):
        review.bind_text_change(self.conn, claim_id="c", gone=self.LONG,
                                parent_commit="abc123")
        got = review.closing_params(self.conn, "c")
        self.assertEqual(got["text_gone"], self.LONG)
        self.assertEqual(got["parent_commit"], "abc123")

    def test_a_marker_short_enough_to_match_by_accident_is_refused(self):
        with self.assertRaises(ValueError) as c:
            review.bind_text_change(self.conn, claim_id="c", gone="wrapper",
                                    parent_commit="abc")
        self.assertIn("floor is", str(c.exception))

    def test_closing_with_neither_direction_is_refused(self):
        with self.assertRaises(ValueError):
            review.bind_text_change(self.conn, claim_id="c", parent_commit="abc")


class EngagementIsAnnouncedAtDerive(unittest.TestCase):
    """The enforcement point and the telling point are not the same point.

    SPEC.md §9's whole argument for this layer is that it lands before the work
    -- refusing before anything is written costs nothing, refusing after twenty
    minutes is a different mechanism at a different price. A claim exists from
    `derive`, so what it will ask for is knowable then; `check` is where it is
    enforced because that is where a claim is answered. `derive` used to say
    nothing, so a worker learned at `check`, having already written the code.
    """

    def _repo(self):
        """A repo whose one detector raises a kind that owes a sentence.

        This ran `derive --task t-eng` against the framework's own checkout.
        Two things followed, both measured: it could not pass on a clone, where
        `.git/v4/ledger.db` does not exist -- CI failed it with `no such task:
        t-eng` -- and every local run appended to the real ledger, which is how
        9,231 of that ledger's 10,494 event rows came to belong to one test.

        `always_` is the registration-free prefix (`detector_protocol.
        UNCONDITIONAL`), so the detector needs no fixtures of its own.
        """
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({"probe": {
            "question_template": "does this hold?",
            "checker": "probe", "detector": "always_probe.py",
            "staleness": "repo", "engagement": True,
            "rule": {"text": "say what this means for this code"},
        }}))
        (tmp / ".v4" / "checkers.json").write_text(json.dumps(
            {"probe": {"path": "checkers/probe.py", "kinds": ["probe"]}}))
        (tmp / "detectors").mkdir()
        (tmp / "detectors" / "always_probe.py").write_text(
            "import argparse, sys\n"
            "p = argparse.ArgumentParser()\n"
            "p.add_argument('--subject'); p.add_argument('--facts')\n"
            "p.add_argument('--out'); p.parse_args()\n"
            "print('V4-CLAIM: kind=probe')\n"
            "sys.exit(0)\n")
        (tmp / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)
        return tmp

    def test_derive_lists_the_claims_that_will_ask_for_a_sentence(self):
        tmp = self._repo()
        conn, cfg = ledger.connect(tmp), config.RepoConfig(tmp)
        self.addCleanup(conn.close)
        lifecycle.open_task(conn, cfg, task_id="t-eng", request="r",
                            scope_globs=["**"])
        out = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, %r); "
             "from kernel import cli; sys.exit(cli.main())" % str(REPO),
             "--repo", str(tmp), "derive", "--task", "t-eng"],
            cwd=tmp, capture_output=True, text=True)
        self.assertIn("need a sentence before", out.stdout, out.stderr)
        self.assertIn("engage --claim", out.stdout)


class AdoptionPath(unittest.TestCase):
    """A repo can be scaffolded, and the scaffold refuses to guess.

    Adopting meant hand-writing three JSON files with no template and finding
    out what was missing one `doctor` complaint at a time -- and `doctor` could
    only detect them because the shapes already existed, so the shapes were
    there and only the writing was not.
    """

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / "app.py").write_text(
            "import requests\n"
            "def send(u):\n"
            "    r = requests.post(u, json={})\n"
            "    return requests.get(u)\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)
        return tmp

    def test_it_writes_the_registries_and_leaves_the_oracle_unanswered(self):
        from kernel import init as init_mod
        tmp = self._repo()
        written = dict(init_mod.scaffold(tmp))
        self.assertEqual(set(map(str, written)),
                         {".v4/config.json", ".v4/claim_kinds.json",
                          ".v4/acceptance.json",
                          ".v4/checkers.json", ".v4/detectors.json"})
        # Four fields nothing outside this repo can answer, and one ruler.
        # `test_command` is the sole oracle for every `test` claim; `criteria`
        # is what a measurement round freezes; `surface_command`, `runtime_proof`
        # and `truth_command` are the two questions that need a live environment
        # and the command that asks the second. A scaffold that guessed any of
        # them hands out a verdict nobody earned.
        #
        # Shaped and unanswered, rather than absent: an absent key reads as a
        # repo with no surface that writes to nothing, and `init` would say
        # nothing about either. Measured on the reference adopter: 7 `ui_globs`
        # and 51 `outbound_write` declared, neither command ever written, and
        # `surface-proof` and `runtime-proof` raised zero claims in 37 tasks.
        self.assertEqual(init_mod.unanswered(tmp),
                         [(".v4/config.json", "runtime_proof"),
                          (".v4/config.json", "surface_command"),
                          (".v4/config.json", "test_command"),
                          (".v4/config.json", "truth_command"),
                          (".v4/acceptance.json", "criteria")])

    def test_no_facts_template_because_one_could_not_validate(self):
        from kernel import init as init_mod
        tmp = self._repo()
        init_mod.scaffold(tmp)
        self.assertEqual(list((tmp / ".v4").glob("facts*.json")), [])

    def test_a_round_cannot_be_opened_against_a_ruler_nobody_wrote(self):
        """Measured: in an adopted repo `v4 round open` died on a raw
        FileNotFoundError, so a whole mechanism was unreachable and said so in
        the least useful way available."""
        from kernel import init as init_mod, ledger, register
        tmp = self._repo()
        init_mod.scaffold(tmp)
        conn = ledger.connect(tmp)
        with self.assertRaises(register.RulerMoved) as caught:
            register.open_round(conn, tmp / ".v4" / "acceptance.json", "r1")
        self.assertIn("TODO", str(caught.exception))

        path = tmp / ".v4" / "acceptance.json"
        obj = json.loads(path.read_text())
        obj["criteria"] = {"red_fixtures_min": 5}
        path.write_text(json.dumps(obj))
        self.assertTrue(register.open_round(conn, path, "r1"))
        conn.close()

    def test_a_second_run_keeps_what_is_there(self):
        from kernel import init as init_mod
        tmp = self._repo()
        init_mod.scaffold(tmp)
        (tmp / ".v4" / "config.json").write_text('{"test_command": "mine"}')
        again = dict(init_mod.scaffold(tmp))
        self.assertEqual(again[Path(".v4/config.json")], "kept")
        self.assertIn("mine", (tmp / ".v4" / "config.json").read_text())

    def test_propose_reads_the_repo_and_marks_every_row_unreviewed(self):
        from kernel import facts as facts_mod
        tmp = self._repo()
        table = facts_mod.propose(tmp)
        writes = {r["pattern"] for r in table["outbound_write"]}
        reads = {r["pattern"] for r in table["outbound_read"]}
        self.assertIn("requests.post", writes)
        self.assertIn("requests.get", reads)
        # A write pattern that also matches a read is refused by validate(), so
        # a proposal that produced one would be a proposal nobody could use.
        self.assertFalse(writes & reads)
        self.assertTrue(all(r["kind"] == "proposed"
                            for r in table["outbound_write"]))

    def test_a_proposal_loads_and_marks_what_it_could_not_answer(self):
        """The demand stays; the place it is made moves.

        This used to assert that a proposal fails to validate, on the grounds
        that an empty `auth_decision` asks a question a scan cannot answer.
        Measured on a first adoption, that refusal blocked the wrong repo: a
        library with no route layer and no outbound write could not adopt at
        all, and the message asked it for entries that do not exist.

        So a proposal now loads, every absence it wrote carries AUTO:, `doctor`
        reports them and `v4 ship` refuses while any remain. The repo can be
        worked in before somebody answers; nothing leaves it.
        """
        from kernel import facts as facts_mod
        tmp = self._repo()
        table = facts_mod.propose(tmp)
        facts_mod.validate(table)                      # loadable, not answered
        absent = table.get("absent") or {}
        self.assertIn("auth_decision", absent)
        self.assertTrue(absent["auth_decision"].startswith("AUTO:"))
        # And it says what was searched, so the next reader can re-run it
        # rather than take the word for it.
        self.assertIn("tracked source file(s)", absent["auth_decision"])

    def test_a_repo_that_really_has_none_can_say_so_and_ship(self):
        from kernel import facts as facts_mod
        tmp = self._repo()
        table = facts_mod.propose(tmp)
        table["absent"]["auth_decision"] = (
            "read every def in app/ at HEAD: no route decorator, no caller-"
            "identity branch, nothing to decide")
        facts_mod.validate(table)
        left = [k for k, v in table["absent"].items() if v.startswith("AUTO:")]
        self.assertNotIn("auth_decision", left)


class DoesAnybodyEngageWithTheStandingRules(unittest.TestCase):
    """A rule nobody acts on is a cost with no reader.  SPEC.md §9.

    The gap this closes was measured on one eight-task run: "External write 要
    留低 audit trail:actor、timestamp、trace id" was on screen for all 25
    `external-write` engagement sentences, and neither the framework arm nor the
    free arm ever produced a trace id. Nothing noticed, because nothing had ever
    asked whether a stated rule changes anything.
    """

    RULES = {
        "external-write": {
            "question_template": "q", "checker": "ext", "staleness": "subject",
            "engagement": True,
            "rule": [
                {"text": "Read-back 要嚟自 truth owner，唔可以係 cache 或者 console 輸出。",
                 "source": "a"},
                {"text": "External write 要留低 audit trail：actor、timestamp、trace id。",
                 "source": "b"},
            ],
        },
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(
            json.dumps(self.RULES, ensure_ascii=False))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        self.cfg = config.RepoConfig(self.tmp)
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _said(self, n, sentence):
        cid = f"c{n}"
        ledger.insert(self.conn, "claim", id=cid, task_id="t",
                      kind="external-write", question="q", subject_refs=[],
                      checker="ext", origin="derive", created_at="2026")
        ledger.insert(self.conn, "event", task_id="t", claim_id=cid,
                      kind="engagement", actor="worker",
                      payload={"sentence": sentence, "verdict": "accepted"},
                      created_at="2026")

    def _rows(self):
        from kernel import doctrine
        return {r["rule"].split("：")[0].split("，")[0]: r
                for r in doctrine.uptake(self.conn, self.cfg)}

    def test_a_rule_nobody_reaches_for_scores_zero(self):
        for i in range(4):
            self._said(i, "個 read-back 由平台自己個 response 讀返，"
                          f"唔係由 cache，第 {i} 次。")
        got = self._rows()
        self.assertEqual(got["Read-back 要嚟自 truth owner"]["engaged"], 4)
        self.assertEqual(got["External write 要留低 audit trail"]["engaged"], 0)

    def test_a_word_the_sibling_rule_also_uses_does_not_count(self):
        """Measured: the audit-trail rule scored 16 of 25 on `external`, `write`
        and `id` -- words in every sentence this kind will ever produce."""
        for i in range(3):
            self._said(i, f"呢個 external write 個 id 係 {i}，冇其他嘢好講。")
        self.assertEqual(self._rows()["External write 要留低 audit trail"]["engaged"], 0)

    def test_reaching_for_the_rules_own_word_counts(self):
        self._said(0, "呢個 write 而家 emit 一行帶住 trace id 同 actor 嘅 log，"
                      "所以之後追得返邊個 send 出事。")
        self.assertEqual(self._rows()["External write 要留低 audit trail"]["engaged"], 1)

    def test_a_rule_with_no_word_of_its_own_is_unmeasured_not_clean(self):
        kinds = {"k": {"question_template": "q", "checker": "c",
                       "staleness": "subject", "engagement": True,
                       "rule": [{"text": "同一句說話。", "source": "a"},
                                {"text": "同一句說話。", "source": "b"}]}}
        (self.tmp / ".v4" / "claim_kinds.json").write_text(
            json.dumps(kinds, ensure_ascii=False))
        from kernel import doctrine
        rows = doctrine.uptake(self.conn, config.RepoConfig(self.tmp))
        self.assertTrue(all(r["engaged"] is None for r in rows))


class LayerBoundary(unittest.TestCase):
    """A contract a module states about itself, that nothing read.

    Every file in `kernel/analysis/` says "pure analysis, no I/O" in its own
    docstring. `external_write.py` imported the whole of `kernel.facts`, which
    opens files and runs git, for two pure functions. One violation across
    eleven cross-layer edges, found the first time anything asked.
    """

    def _cfg(self):
        return json.loads((REPO / ".v4" / "layers.json").read_text())

    def test_this_repo_holds_to_its_own_declaration(self):
        from kernel.analysis import layers
        self.assertEqual(layers.scan(REPO, self._cfg()), [])

    def test_the_analysis_layer_no_longer_reaches_the_loader(self):
        # The fix was to split kernel/facts.py, not to allow the edge.
        from kernel.analysis import pysource
        src = (REPO / "kernel" / "analysis" / "external_write.py").read_text()
        self.assertNotIn("kernel.facts", pysource.imported_modules(src))

    def test_the_grammar_and_the_loader_are_the_same_functions(self):
        # Re-export, not reimplementation: two copies of a matcher is two
        # answers, which is what `kernel/analysis/` exists to prevent.
        from kernel import facts
        from kernel.analysis import facts_grammar
        for name in ("scan_source", "build", "validate", "matches_symbol"):
            self.assertIs(getattr(facts, name), getattr(facts_grammar, name), name)

    def test_an_edge_the_declaration_omits_is_reported(self):
        from kernel.analysis import layers
        narrowed = json.loads(json.dumps(self._cfg()))
        narrowed["allow"] = [e for e in narrowed["allow"]
                             if e != ["checkers", "kernel"]]
        found = layers.scan(REPO, narrowed)
        self.assertTrue(found)
        self.assertTrue(all(v[2] == "checkers" and v[3] == "kernel" for v in found),
                        found[:3])

    def test_no_declaration_is_unsupported_not_clean(self):
        from kernel.analysis import layers
        self.assertIsNone(layers.scan(REPO, {"layers": [], "allow": []}))


class LensBrief(unittest.TestCase):
    """What a reviewer is actually handed.

    Two defects lived here at once. Seven of the nine lens files hold plain
    strings and two hold objects, and the object shape was rendered with an
    f-string -- so 130 of the 280 checks reached the reviewer as a Python dict
    literal, including `why_not_a_checker`, which is the argument for not
    looking. And the brief told the reviewer to print a `V4-CLAIM:` line, which
    only a detector's stdout is parsed for; the agent prompt had the same defect
    and was fixed, and the function that generates the brief kept it.
    """

    def _lenses(self):
        return sorted((REPO / ".v4" / "lenses").glob("*.json"))

    def test_no_check_reaches_a_reviewer_as_a_dict_literal(self):
        for path in self._lenses():
            lens = json.loads(path.read_text())
            brief = review.lens_brief(lens)
            self.assertNotIn("{'check':", brief, path.name)
            self.assertNotIn("'why_not_a_checker'", brief, path.name)

    def test_every_check_still_appears(self):
        for slug, lens in review.lenses(REPO).items():
            brief = review.lens_brief(lens, task="review-task")
            for check in lens["checks"]:
                self.assertIn(check["id"], brief, slug)
                self.assertIn(review.check_text(check).replace("$V4_TASK", "review-task"), brief, slug)
            for guidance in lens.get("reviewer_anti_patterns", []):
                self.assertIn(guidance, brief, slug)

    def test_it_names_the_entry_point_that_exists(self):
        lens = json.loads(self._lenses()[0].read_text())
        brief = review.lens_brief(lens)
        self.assertIn("v4 --repo . review add", brief)
        self.assertNotIn("V4-CLAIM:", brief)

    def test_an_object_check_renders_its_check_text(self):
        out = review.check_text(
            {"check": "the rule", "why_not_a_checker": "no instance today",
             "from": "R-1"})
        self.assertEqual(out, "the rule")


class FiltersMatchingNothing(unittest.TestCase):
    """A glob that names something and matches nothing.

    The two-run version of this guard compared "with your table" against "with
    no table" and read every legitimate narrowing as an attack. Measured: the
    claims it called hidden were the detector scanning `tests/` --
    `entrypoint_globs` doing its job -- and `external_write` was refused on
    every round of two whole runs of the three-arm experiment.
    """

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / "app").mkdir()
        (tmp / "app" / "main.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, check=True)
        return tmp

    def test_a_glob_that_matches_a_real_file_is_fine(self):
        tmp = self._repo()
        self.assertEqual(
            derive._filters_matching_nothing(tmp, {"entrypoint_globs": ["app/**"]}), [])

    def test_a_glob_pointed_at_nothing_is_reported(self):
        tmp = self._repo()
        out = derive._filters_matching_nothing(
            tmp, {"ui_globs": ["web/does_not_exist/**"]})
        self.assertEqual(len(out), 1)
        self.assertIn("ui_globs", out[0])

    def test_an_empty_list_is_a_statement_not_a_dead_glob(self):
        # FACTS.md: `[]` is a repo saying the surface is not here. The key is
        # required, so that is a statement rather than an omission.
        tmp = self._repo()
        self.assertEqual(derive._filters_matching_nothing(tmp, {"ui_globs": []}), [])

    def test_a_short_vocabulary_is_not_a_filter(self):
        # A repo listing three outbound symbols where the built-in default has
        # 26 is being specific, not being disarmed. Vocabulary is not checked.
        tmp = self._repo()
        self.assertEqual(derive._filters_matching_nothing(
            tmp, {"outbound_write": [{"pattern": "requests.post"}]}), [])


class AccountingForTheRequestItself(unittest.TestCase):
    """Nothing in this framework ever read `task.request`.  SPEC.md §4.6.

    Measured on an eight-task X/Y run: three of the eight briefs came back with
    less than they asked for -- no way to send a file from disk, no way to read
    what a local Bot API server answers, and a bug report answered by making the
    bug expressible rather than fixing it -- and not one mechanism raised a
    claim. `task.request` was written in two places and read in none, which is
    the shape this project's own `dead-wiring` checker exists to find.

    What is deliberately not built here is a checker that reads a request and
    decides whether it was met. Deriving obligations from prose is what the
    predecessor died of. This asks a narrower question a program can answer:
    how much of the request has somebody quoted back and said something about.
    """

    REQ = "個 client 要識 send 相同片。另外要有個一次過 send 一組嘅做法。"

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        self.conn = ledger.connect(self.tmp)
        ledger.insert(self.conn, "task", id="t", request=self.REQ,
                      scope_globs=["**"], base_commit="x", created_at="2026")

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cover(self, **kw):
        from kernel import request_cover
        # A delivered piece owes an acceptance condition. These cases are about
        # the accounting half, so they supply one and say nothing more about it.
        if not kw.get("not_done"):
            kw.setdefault("acceptance", "the named symbol returns what this "
                                        "piece of the request describes")
        return request_cover.record(self.conn, task_id="t", request=self.REQ, **kw)

    def _ratio(self):
        from kernel import request_cover
        return request_cover.measure(
            self.REQ, request_cover.entries(self.conn, "t"))["ratio"]

    def test_a_paraphrase_is_refused(self):
        """An account that can drift from the thing it accounts for is not one."""
        from kernel import request_cover
        with self.assertRaises(request_cover.NotInTheRequest) as c:
            self._cover(quote="send photos and videos", symbol="send_photo")
        self.assertIn("not in this task's request", str(c.exception))

    def test_a_quoted_piece_counts_toward_the_request(self):
        self.assertEqual(self._ratio(), 0.0)
        self._cover(quote="個 client 要識 send 相同片", symbol="send_photo")
        self.assertGreater(self._ratio(), 0.3)
        self.assertLess(self._ratio(), 0.75)

    def test_deciding_not_to_do_a_piece_is_allowed_but_not_silently(self):
        from kernel import request_cover
        with self.assertRaises(request_cover.NotInTheRequest) as c:
            self._cover(quote="另外要有個一次過 send 一組嘅做法",
                        not_done=True, why="唔做住")
        self.assertIn("not allowed to be silent", str(c.exception))

        self._cover(quote="個 client 要識 send 相同片", symbol="send_photo")
        self._cover(quote="另外要有個一次過 send 一組嘅做法", not_done=True,
                    why="sendMediaGroup 要 attach:// multipart，而呢個 client 而家一個"
                        "上載路徑都冇。一組嗰半留返下一個 task 做。")
        self.assertEqual(self._ratio(), 1.0)

    def test_delivered_has_to_name_something(self):
        from kernel import request_cover
        with self.assertRaises(request_cover.NotInTheRequest) as c:
            self._cover(quote="個 client 要識 send 相同片")
        self.assertIn("names the symbol", str(c.exception))

    def test_quoting_the_same_span_twice_does_not_count_twice(self):
        """Otherwise five entries on one clause read as a finished reading."""
        self._cover(quote="個 client 要識 send 相同片", symbol="send_photo")
        once = self._ratio()
        self._cover(quote="send 相同片", symbol="send_photo")
        self._cover(quote="要識 send", symbol="send_photo")
        self.assertEqual(self._ratio(), once)


class TheSentenceForCodeThatDoesNotExistYet(unittest.TestCase):
    """Half a task's claims cannot be engaged with first, and the rule can.

    A claim about code that already exists is raised at the first derive --
    measured, 37 of 43 across six tasks -- and the write hook holds every write
    until those have sentences. A claim about code this task is creating cannot
    exist until the code does: `fail-closed` on a new worker's `_send` was
    raised seventeen minutes after the file was first written. For that half the
    sentence has to be written against the kind's rule instead, and its subject
    is the task's scope, because "the code I am about to write" is a set of
    paths before it is anything else.
    """

    SCOPED = "core/workers/replies.py"

    def _repo(self):
        from kernel import config, ledger, lifecycle
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8}}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"fail-closed": {"checker": "fail-closed", "detector": None,
                             "staleness": "subject", "engagement": True,
                             "question_template": "q",
                             "rule": [{"text": "an except that lets the caller "
                                               "believe the work happened is open"}]},
             "lint": {"checker": "lint", "detector": None,
                      "staleness": "repo", "engagement": False,
                      "question_template": "q"}}))
        conn, cfg = ledger.connect(tmp), config.RepoConfig(tmp)
        lifecycle.open_task(conn, cfg, task_id="t-pre", request="build a worker",
                            scope_globs=[self.SCOPED])
        return conn, cfg

    def test_a_sentence_naming_what_it_will_write_is_accepted(self):
        from kernel import engagement
        conn, cfg = self._repo()
        ok, why = engagement.judge_before(
            conn, cfg, task_id="t-pre", kind="fail-closed",
            sentence=(f"{self.SCOPED} will separate sent from unknown: sent only "
                      f"with a message id, and an interrupted send stays unknown "
                      f"rather than being retried into a second message."))
        self.assertTrue(ok, why)
        self.assertIn("fail-closed", engagement.before_the_work(conn, "t-pre"))

    def test_who_wrote_it_is_on_the_record(self):
        """The whole argument for this event is that a sentence handed down by
        whoever cut the task is a constraint, while the same sentence from the
        agent about to write the code is self-justification a few minutes
        earlier. Recorded as `worker` for everyone, that is not askable -- and it
        was hardcoded that way for a day, during which three tasks wrote pre-work
        sentences whose author cannot now be established."""
        from kernel import engagement
        conn, cfg = self._repo()
        ok, why = engagement.judge_before(
            conn, cfg, task_id="t-pre", kind="fail-closed", actor="splitter",
            sentence=(f"{self.SCOPED} will separate sent from unknown, and an "
                      f"interrupted send stays unknown rather than being retried "
                      f"into a second message."))
        self.assertTrue(ok, why)
        actor, _ = engagement.before_the_work(conn, "t-pre", "fail-closed")
        self.assertEqual(actor, "splitter")
        row = conn.execute(
            "SELECT actor FROM event WHERE kind = ?",
            (engagement.BEFORE_KIND,)).fetchone()
        self.assertEqual(row["actor"], "splitter",
                         "the ledger column has to agree with the payload")

    def test_an_actor_nobody_recognises_is_refused(self):
        from kernel import engagement
        conn, cfg = self._repo()
        ok, why = engagement.judge_before(
            conn, cfg, task_id="t-pre", kind="fail-closed", actor="reviewer",
            sentence=f"{self.SCOPED} will do the right thing at the boundary "
                     f"and say so when it cannot.")
        self.assertFalse(ok)
        self.assertIn("not one of", why)

    def test_a_sentence_about_nothing_in_particular_is_refused(self):
        """The same test every other sentence gets. Writing one first does not
        make it right -- it makes it a position on the record before the code."""
        from kernel import engagement
        conn, cfg = self._repo()
        ok, why = engagement.judge_before(
            conn, cfg, task_id="t-pre", kind="fail-closed",
            sentence=("every outbound call needs a way to know whether it landed, "
                      "and not raising is not the same answer as arriving."))
        self.assertFalse(ok)
        self.assertIn("mentions nothing this task is scoped to", why)

    def test_a_refusal_is_on_the_record_too(self):
        from kernel import engagement
        conn, cfg = self._repo()
        engagement.judge_before(conn, cfg, task_id="t-pre", kind="fail-closed",
                                sentence="too short")
        rows = list(conn.execute(
            "SELECT payload FROM event WHERE kind = ?", (engagement.BEFORE_KIND,)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["payload"])["verdict"], "refused")
        self.assertEqual(engagement.before_the_work(conn, "t-pre"), {})

    def test_a_kind_with_no_rule_has_nothing_to_engage_with(self):
        from kernel import engagement
        conn, cfg = self._repo()
        ok, why = engagement.judge_before(
            conn, cfg, task_id="t-pre", kind="lint",
            sentence=f"{self.SCOPED} will stay inside its own package boundary "
                     f"and import nothing private from another one.")
        self.assertFalse(ok)
        self.assertIn("carries no rule", why)


class AFindingWithNoTaskStillLands(unittest.TestCase):
    """`claim.task_id` is NOT NULL and a periodic sweep has no task.

    `v4 review lens` says so in as many words and was fixed to record without
    one; three lines later `review add` still joined a `None` into
    `hashing.claim_id`. Measured on one adopter: nine lenses ran, the sweep
    recorded 61 findings, and `claim WHERE kind = 'review-finding'` held zero.
    """

    def _repo(self):
        from kernel import config, ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / "app").mkdir()
        (tmp / "app" / "w.py").write_text(
            "def send(x):\n    try:\n        go(x)\n    except Exception:\n"
            "        pass\n")
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8}}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"review-finding": {"checker": "review-finding", "detector": None,
                                "staleness": "subject", "engagement": True,
                                "question_template": "is {file}::{symbol} closed"}}))
        return ledger.connect(tmp), config.RepoConfig(tmp)

    def test_a_task_less_finding_reaches_the_ledger(self):
        from kernel import ledger, review
        conn, cfg = self._repo()
        cid, created, _ = review.raise_finding(
            conn, cfg, task_id=None, file="app/w.py", symbol="send",
            note="the except swallows and control leaves without propagating")
        self.assertTrue(created)
        row = conn.execute("SELECT task_id FROM claim WHERE id = ?",
                           (cid,)).fetchone()
        self.assertEqual(row["task_id"], ledger.REVIEW_TASK)

    def test_two_worktrees_filing_at_once_do_not_collide(self):
        """The ledger is shared across worktrees and `task.id` is a PRIMARY KEY,
        so "read, see nothing, insert" is a race the framework's own notes invite
        people into by telling them to run in parallel.

        Real processes, not threads: the collision is between two connections to
        one file, and a thread pool in one interpreter can share a connection and
        miss it. Measured on the old code, eight processes: four died on
        `UNIQUE constraint failed: task.id`.
        """
        import concurrent.futures as cf
        conn, cfg = self._repo()
        root = cfg.root
        conn.close()

        script = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, script, ignore_errors=True)
        (script / "one.py").write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(REPO)!r})\n"
            "from kernel import ledger, review\n"
            f"conn = ledger.connect({str(root)!r})\n"
            "print(review.ensure_review_task(conn))\n")

        def run(_):
            return subprocess.run([sys.executable, str(script / "one.py")],
                                  capture_output=True, text=True, timeout=60)
        with cf.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))
        failed = [r for r in results if r.returncode != 0]
        self.assertEqual(failed, [], "\n".join(r.stderr[-300:] for r in failed))
        self.assertEqual({r.stdout.strip() for r in results}, {"repo-review"})

    def test_the_row_it_hangs_from_is_never_open_work(self):
        """A hook guarding it would guard every repo forever, and `doctor`
        would report an unshipped task nobody can ship."""
        from kernel import ledger, review
        conn, cfg = self._repo()
        review.raise_finding(conn, cfg, task_id=None, file="app/w.py",
                             symbol="send", note="n")
        self.assertIsNone(ledger.open_task_id(conn))

    def test_the_same_finding_next_sweep_is_the_same_claim(self):
        """`claim_id` mixes in the task so two tasks on one call site do not
        collapse. For a sweep that inverts: finding it again is not a new
        finding, and raising one every four days is how a list stops being read."""
        from kernel import review
        conn, cfg = self._repo()
        first, made, _ = review.raise_finding(conn, cfg, task_id=None,
                                              file="app/w.py", symbol="send", note="n")
        again, made_again, _ = review.raise_finding(conn, cfg, task_id=None,
                                                    file="app/w.py", symbol="send",
                                                    note="n")
        self.assertEqual(first, again)
        self.assertTrue(made)
        self.assertFalse(made_again)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM claim").fetchone()[0], 1)

    def test_doctor_says_a_finding_is_open(self):
        """Filing it is not the same as anybody seeing it.

        The review row is deliberately outside "open tasks" -- a hook guarding it
        would guard every repo forever -- and that exclusion is why nothing
        showed these. Measured the day filing started working: one finding
        landed, sat unrun, and `doctor` said nothing, so it was as visible in the
        ledger as in the markdown file it came from.
        """
        from kernel import doctor, review
        conn, cfg = self._repo()
        review.raise_finding(conn, cfg, task_id=None, file="app/w.py",
                             symbol="send", note="n")
        conn.commit()
        conn.close()
        line = [c for c in doctor.run(cfg.root) if c["what"] == "review findings"]
        self.assertTrue(line, "doctor said nothing about an open finding")
        self.assertIn("app/w.py::send", line[0]["detail"])

    def test_the_sweep_record_counts_what_reached_the_ledger(self):
        """`lenses` was cross-checked against `ran` and `findings` was not, so
        the record said 61 while the ledger held none of them."""
        from kernel import review, sweep
        conn, cfg = self._repo()
        review.raise_finding(conn, cfg, task_id=None, file="app/w.py",
                             symbol="send", note="n")
        sweep.record(conn, lenses=["prevention", "devx"], findings=9)
        payload = json.loads(conn.execute(
            "SELECT payload FROM event WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (sweep.KIND,)).fetchone()["payload"])
        self.assertEqual(payload["findings"], 9)
        self.assertEqual(payload["raised_since_last_sweep"], 1)

    def test_a_note_written_wrong_can_be_corrected(self):
        """The one fact about a claim that could never be fixed.

        A claim id comes from where the finding is and deliberately not from what
        it says, so that one defect keeps one identity across a `scope widen`.
        The cost was that re-filing with a corrected note hit "already exists",
        returned, and exited 0 -- the note went nowhere and the command said
        nothing was wrong. Every other after-the-fact fact about a claim has an
        event; this had none, so the correction ended up in `.v4/deferred/`, a
        ledger correction living outside the ledger.

        The correction is still an event and still leaves both readings on the
        record. What moved is which command asks for it: it used to be inferred
        from re-filing, and the inference destroyed six answered findings on
        2026-08-27, because "I am correcting what I wrote" and "I have found a
        second thing here" reach `raise_finding` looking identical. So the
        route is `amend_note`, named, and the same assertions are made of it.
        """
        from kernel import review
        conn, cfg = self._repo()
        cid, created, _siblings = review.raise_finding(
            conn, cfg, task_id=None, file="app/w.py", symbol="send",
            note="probe: does a task-less sweep finding file now?")
        self.assertTrue(created)

        same, created, _siblings = review.raise_finding(
            conn, cfg, task_id=None, file="app/w.py", symbol="send",
            note="probe: does a task-less sweep finding file now?")
        self.assertEqual((same, created), (cid, False))

        was, changed = review.amend_note(
            conn, claim_id=cid, actor="agent",
            note="the allowlist rationale still names a refactor that has landed")
        self.assertTrue(changed)
        self.assertIn("probe:", was)

        row = conn.execute(
            "SELECT payload FROM event WHERE claim_id = ? AND kind = ?",
            (cid, review.AMENDED_KIND)).fetchone()
        payload = json.loads(row["payload"])
        self.assertIn("probe:", payload["was"])
        self.assertIn("allowlist rationale", payload["now"])
        # The claim row is untouched -- the ledger takes no updates, and both
        # readings stay on the record.
        self.assertIn("probe:", conn.execute(
            "SELECT note FROM claim WHERE id = ?", (cid,)).fetchone()["note"])
        # And the corrected text is what the next sweep is measured against:
        # re-filing it is the same finding, not a second one.
        again, created, _siblings = review.raise_finding(
            conn, cfg, task_id=None, file="app/w.py", symbol="send",
            note="the allowlist rationale still names a refactor that has landed")
        self.assertEqual((again, created), (cid, False))

    def test_the_count_is_named_for_the_window_it_measures(self):
        """It counts every review finding since the last sweep, not this sweep's.

        A reviewer reading a diff between sweeps files into the same window, so
        the number is a floor. Calling it `raised` invited the one reading that
        makes it useless: this field exists to catch "claimed many, raised few",
        and a sweep that raised nothing looks like it worked if somebody else
        filed in between. The name is the fix, not a sweep id -- that is a new
        concept every caller must know it is inside, for a precision problem
        that has not misled anyone yet.
        """
        from kernel import review, sweep
        conn, cfg = self._repo()
        sweep.record(conn, lenses=["prevention"], findings=0)   # raised nothing
        review.raise_finding(conn, cfg, task_id=None, file="app/w.py",
                             symbol="send", note="filed by a reviewer, not the sweep")
        sweep.record(conn, lenses=["prevention"], findings=0)   # raised nothing
        payload = json.loads(conn.execute(
            "SELECT payload FROM event WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (sweep.KIND,)).fetchone()["payload"])
        self.assertEqual(payload["findings"], 0)
        self.assertEqual(payload["raised_since_last_sweep"], 1)   # not this sweep's
        self.assertNotIn("raised", payload)


class WhatACarriedRequestIsNotAskingFor(unittest.TestCase):
    """`--after` carries the previous request in; coverage must not re-ask it.

    Measured on a six-task chain before this: 42, 129, 197, 307, 386, 501
    clauses, while the sixth task changed four files. The fifth answered 386 by
    delivering 50 and writing 293 `--not-done` entries saying "that belonged to
    an earlier cut". The carried block is context for the worker, not a request
    for this task to account for.
    """

    MINE = "加一個 keyword 欄落 lead_magnets。順手寫返個 index。"
    CARRIED = (
        "\n\n[continues t-earlier]\n"
        "  its request: 起一個 PDF renderer。順手接埋個 transform registry。\n"
        "  its scope  : core/render.py, tests/test_render.py\n"
        "  answered   : dependency, scope, secret\n"
    )

    def test_only_this_task_s_own_clauses_are_counted(self):
        from kernel import request_cover
        alone = request_cover.clauses(self.MINE)
        chained = request_cover.clauses(self.MINE + self.CARRIED)
        self.assertEqual(alone, chained)

    def test_a_chain_does_not_make_the_denominator_grow(self):
        from kernel import request_cover
        one = request_cover.measure(self.MINE, [])["total"]
        two = request_cover.measure(self.MINE + self.CARRIED, [])["total"]
        self.assertEqual(one, two)

    def test_a_request_with_no_carried_block_is_untouched(self):
        from kernel import request_cover
        self.assertEqual(request_cover.own(self.MINE), self.MINE)

    def test_quoting_the_carried_block_says_why_it_does_not_count(self):
        """Not "that is not in the request" -- it is, and the distinction is the
        whole point. A worker that reads the wrong message writes the entry
        again with a different span."""
        from kernel import request_cover
        bad = request_cover.fault(
            self.MINE + self.CARRIED,
            {"quote": "起一個 PDF renderer", "symbol": "x", "acceptance": "y"})
        self.assertIn("carried in from the task this one continues", bad)

    def test_a_request_that_is_nothing_but_carried_context_still_measures(self):
        """Degenerate, but a zero denominator is worse than the old behaviour."""
        from kernel import request_cover
        only = self.CARRIED.strip()
        self.assertTrue(request_cover.own(only))
        self.assertGreater(request_cover.measure(only, [])["total"], 0)


class TheAfterGateIsPeriodic(unittest.TestCase):
    """When a sweep is due, and when now is the wrong moment.  SPEC.md §10.1.

    Measured on an eight-task X/Y run: nine lenses on every task cost 6.6x, and
    the arm that paid it shipped three of eight briefs short. The same lenses
    run once at the end took production-incident findings from three to zero for
    1.96x. So the after-gate is periodic, and the only two questions a program
    can answer about it are whether it is due and whether the tree is settled.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _git_repo(self.tmp)
        (self.tmp / ".v4").mkdir()
        (self.tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "lens_sweep": {"every_days": 4}}))
        (self.tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"test": {"question_template": "q", "checker": "test",
                      "staleness": "subject"}}))
        (self.tmp / ".v4" / "checkers.json").write_text("{}")
        self.cfg = config.RepoConfig(self.tmp)
        self.conn = ledger.connect(self.tmp)

    def tearDown(self):
        self.conn.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _due(self, **kw):
        from kernel import sweep
        return sweep.due(self.conn, config.RepoConfig(self.tmp), **kw)

    def _task(self, tid, *, open_claim=False):
        ledger.insert(self.conn, "task", id=tid, request="r", scope_globs=["**"],
                      base_commit="x", created_at="2026")
        if open_claim:
            ledger.insert(self.conn, "claim", id=f"c-{tid}", task_id=tid,
                          kind="test", question="q", subject_refs=[],
                          checker="test", origin="derive", created_at="2026")

    def _config(self, **kw):
        p = self.tmp / ".v4" / "config.json"
        c = json.loads(p.read_text())
        c["lens_sweep"] = kw
        p.write_text(json.dumps(c))

    def test_a_repo_that_has_never_swept_is_due(self):
        ok, why = self._due()
        self.assertTrue(ok, why)
        self.assertIn("never", why)

    def test_a_task_still_holding_a_claim_is_the_wrong_moment(self):
        """A review of a half-written tree reports the half as a finding."""
        self._task("t1", open_claim=True)
        ok, why = self._due()
        self.assertFalse(ok)
        self.assertIn("unanswered claim", why)

    def test_a_finished_task_does_not_hold_it(self):
        self._task("t1")
        self.assertTrue(self._due()[0])

    def test_the_interval_holds_until_it_has_passed(self):
        from kernel import sweep
        self._lens_event("lens_reviewed", "devx", "2020-01-01T00:00:00+00:00", findings=3)
        sweep.record(self.conn, lenses=["devx"], findings=3)
        ok, why = self._due()
        self.assertFalse(ok)
        self.assertIn("interval", why)
        later = datetime.now(timezone.utc) + timedelta(days=5)
        self.assertTrue(self._due(now=later)[0])

    def test_a_weekday_it_is_not_holds_it(self):
        today = datetime.now(timezone.utc).weekday()
        self._config(every_days=0, weekday=(today + 1) % 7)
        ok, why = self._due()
        self.assertFalse(ok)
        self.assertIn("weekday", why)

    def test_an_hour_it_is_not_yet_holds_it(self):
        self._config(every_days=0, not_before_hour=23)
        at_nine = datetime.now(timezone.utc).replace(hour=9)
        ok, why = self._due(now=at_nine)
        self.assertFalse(ok)
        self.assertIn("23:00", why)

    def test_a_task_nobody_touched_since_the_last_sweep_is_not_a_hold(self):
        """Every task ever was the first version, and one abandoned demo task
        would then have held every future sweep -- the same trap `doctor` was
        reporting counts of before it started naming them."""
        from kernel import sweep
        self._task("old", open_claim=True)
        # `detector_run`, which is what `derive` actually writes. This fixture
        # said `derive`, a name nothing in the kernel emits, so the row it built
        # to stand for "something touched this task" was a shape the sweep never
        # meets in a real ledger. `EVENT_KINDS` refuses it now.
        ledger.insert(self.conn, "event", task_id="old", claim_id=None,
                      kind="detector_run", actor="kernel", payload={},
                      created_at="2020-01-01T00:00:00+00:00")
        self._lens_event("lens_reviewed", "devx", "2020-01-02T00:00:00+00:00", findings=0)
        sweep.record(self.conn, lenses=["devx"])
        later = datetime.now(timezone.utc) + timedelta(days=5)
        ok, why = self._due(now=later)
        self.assertTrue(ok, why)

    def test_an_unreadable_signature_covers_nothing_instead_of_crashing(self):
        """Found by the sweep, in this repo's own ledger: one `accepted_risk`
        row whose `subject_digest` is the single character `d`. Every command
        that resolves state -- `status`, `ship`, the sweep -- died on that task
        with a JSONDecodeError, and the ledger is append-only so the row can
        never be corrected."""
        self._task("t1", open_claim=True)
        with ledger.writing(self.conn):
            self.conn.execute(
                "INSERT INTO accepted_risk (claim_id, kind, who, why, was_tty, "
                "git_record, subject_digest, created_at) VALUES (?,?,?,?,?,?,?,?)",
                ("c-t1", "unprovable", "h@x", "w" * 50, 1, ".v4/risks/x.json",
                 "d", "2026"))
            self.conn.commit()
        row = self.conn.execute("SELECT * FROM claim WHERE id='c-t1'").fetchone()
        got = state.claim_state(self.conn, self.tmp, row, kinds_cfg=self.cfg.kinds,
                                config_sha=self.cfg.sha,
                                checker_sha_of=self.cfg.checker_sha_on_disk)
        self.assertEqual(got, state.OPEN)   # not RISK_ACCEPTED, and not a crash


    def test_a_sweep_records_what_actually_ran_not_only_what_it_claims(self):
        """The X/Y run put 144 lens agents through `v4 review lens` and the
        ledger held zero `lens_run` rows, because the event was written only
        when `--task` was passed -- and a reviewer reading a diff names no task,
        nor does the periodic sweep, which by design has none."""
        from kernel import sweep
        ledger.insert(self.conn, "event", task_id=None, claim_id=None,
                      kind="lens_run", actor="reviewer",
                      payload={"lens": "devx", "checks": 15},
                      created_at="2026-01-01T00:00:00+00:00")
        ledger.insert(self.conn, "event", task_id=None, claim_id=None,
                      kind="lens_run", actor="reviewer",
                      payload={"lens": "prevention", "checks": 115},
                      created_at="2026-01-01T00:01:00+00:00")
        sweep.record(self.conn, lenses=["devx", "prevention", "security-permission"],
                     findings=4)
        (_when, p), = sweep.history(self.conn)
        self.assertEqual(p["lenses"],
                         ["devx", "prevention", "security-permission"])
        self.assertEqual(p["ran"], ["devx", "prevention"])   # the ledger's answer

    def _lens_event(self, kind, lens, at, task_id=None, **payload):
        ledger.insert(self.conn, "event", task_id=task_id, claim_id=None,
                      kind=kind, actor="reviewer",
                      payload=dict(payload, lens=lens), created_at=at)

    def test_a_sweep_records_who_came_back_beside_who_took_a_brief(self):
        """`ran` was the whole of what the ledger said, and it comes from
        `lens_run` -- which is written when the brief prints, and printing one
        is free. So a sweep where two reviewers took a brief and neither came
        back was stored as `ran: [two lenses]`, which is the self-report this
        layer exists to replace with the ledger's own answer."""
        from kernel import sweep
        self._lens_event("lens_run", "devx", "2026-01-01T00:00:00+00:00",
                         checks=15)
        self._lens_event("lens_run", "prevention", "2026-01-01T00:01:00+00:00",
                         checks=115)
        self._lens_event("lens_reviewed", "devx", "2026-01-01T00:30:00+00:00",
                         findings=0)
        sweep.record(self.conn, lenses=["devx", "prevention"], findings=0)
        (_when, p), = sweep.history(self.conn)
        self.assertEqual(p["ran"], ["devx", "prevention"])
        self.assertEqual(p["reviewed"], {"devx": 0},
                         "prevention printed a brief and nobody read a diff "
                         "with it; 0 is devx saying it found nothing")

    def test_a_lens_read_against_a_task_is_not_a_sweeps_coverage(self):
        """A sweep reads the tree as it stands and has no task; `v4 review
        lens --task T` reads T's diff. Measured on this repo at the sweep of
        2026-08-27: eleven lenses were opened for it, and the count printed was
        twelve, because `request-fidelity` had been briefed on `fw-rust`,
        `fw-reqfid` and `fw-tax` -- three workers reading three diffs, counted
        as coverage of a tree none of them was looking at."""
        from kernel import sweep
        self._lens_event("lens_run", "request-fidelity",
                         "2026-01-01T00:00:00+00:00", task_id="fw-tax", checks=6)
        self._lens_event("lens_reviewed", "request-fidelity",
                         "2026-01-01T00:30:00+00:00", task_id="fw-tax", findings=1)
        sweep.record(self.conn, lenses=["request-fidelity"], findings=0)
        (_when, p), = sweep.history(self.conn)
        self.assertEqual(p["ran"], [])
        self.assertEqual(p["reviewed"], {})

    def test_a_second_reading_of_one_lens_replaces_the_first(self):
        """A reviewer running again after a repair is saying something newer,
        not something additional -- the same rule `lifecycle._lenses_reviewed`
        holds the ship side to."""
        from kernel import sweep
        self._lens_event("lens_reviewed", "devx", "2026-01-01T00:10:00+00:00",
                         findings=9)
        self._lens_event("lens_reviewed", "devx", "2026-01-01T00:20:00+00:00",
                         findings=2)
        sweep.record(self.conn, lenses=["devx"], findings=2)
        (_when, p), = sweep.history(self.conn)
        self.assertEqual(p["reviewed"], {"devx": 2})

    def test_only_the_runs_since_the_last_sweep_count(self):
        from kernel import sweep
        ledger.insert(self.conn, "event", task_id=None, claim_id=None,
                      kind="lens_run", actor="reviewer",
                      payload={"lens": "devx", "checks": 15},
                      created_at="2020-01-01T00:00:00+00:00")
        sweep.record(self.conn, lenses=["devx"])
        sweep.record(self.conn, lenses=["devx"])
        newest, older = sweep.history(self.conn)
        self.assertEqual(older[1]["ran"], ["devx"])   # the old run counted once
        self.assertEqual(newest[1]["ran"], [])        # and not again

    def test_and_a_review_counts_once_for_the_sweep_it_landed_in(self):
        """The same window, on the half that says a reviewer came back. A
        `lens_reviewed` row re-credited to every later sweep would make one
        reading of one tree look like coverage forever -- which is the shape
        this pair exists to stop, one column over."""
        from kernel import sweep
        self._lens_event("lens_reviewed", "devx", "2020-01-01T00:00:00+00:00",
                         findings=3)
        sweep.record(self.conn, lenses=["devx"], findings=3)
        sweep.record(self.conn, lenses=["devx"], findings=0)
        newest, older = sweep.history(self.conn)
        self.assertEqual(older[1]["reviewed"], {"devx": 3})
        self.assertEqual(newest[1]["reviewed"], {})

    def test_a_sweep_is_on_the_record_with_what_it_covered(self):
        from kernel import sweep
        sweep.record(self.conn, lenses=["devx", "prevention"], findings=11,
                     note="weekly")
        (when, p), = sweep.history(self.conn)
        self.assertEqual(p["lenses"], ["devx", "prevention"])
        self.assertEqual(p["findings"], 11)
        self.assertEqual(p["note"], "weekly")


class TheDigestDoesNotCountWhatTheRunProduces(unittest.TestCase):
    """A staleness key the act of answering moves is not a staleness key.

    Measured on a first adoption: `test` returned SUBJECT_MOVED on every first
    run and PASS on every second, deterministically. Running the suite writes
    `__pycache__/*.pyc`, those are untracked, `git ls-files --others` lists
    them, and the digest taken after the run differs from the one taken before.
    The second run only worked because the artefacts already existed -- so the
    check that was supposed to catch a tree changing mid-run was instead
    guaranteed to fire once and then never again.
    """

    def _repo(self):
        import subprocess
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / "app").mkdir()
        (tmp / "app" / "m.py").write_text("X = 1\n")
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"derive_exclude": ["**/__pycache__/**"]}))
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, capture_output=True)
        return tmp

    def test_an_excluded_artefact_does_not_move_the_digest(self):
        from kernel import hashing
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        cache = tmp / "app" / "__pycache__"
        cache.mkdir()
        (cache / "m.cpython-311.pyc").write_bytes(b"\x00compiled")
        self.assertEqual(hashing.worktree_digest(tmp), before)

    def test_a_caller_can_still_ask_for_nothing_excluded(self):
        from kernel import hashing
        tmp = self._repo()
        before = hashing.worktree_digest(tmp, ())
        cache = tmp / "app" / "__pycache__"
        cache.mkdir()
        (cache / "m.cpython-311.pyc").write_bytes(b"\x00compiled")
        self.assertNotEqual(hashing.worktree_digest(tmp, ()), before)

    def test_real_source_still_moves_it(self):
        from kernel import hashing
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        (tmp / "app" / "m.py").write_text("X = 2\n")
        self.assertNotEqual(hashing.worktree_digest(tmp), before)

    def test_an_untracked_source_file_still_moves_it(self):
        from kernel import hashing
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        (tmp / "app" / "n.py").write_text("Y = 1\n")
        self.assertNotEqual(hashing.worktree_digest(tmp), before)


class AFallbackKeepsTheReposExclusions(unittest.TestCase):
    """`derive_exclude` is stated once and nine checkers re-derive around it."""

    def subject(self, globs):
        return {"params": {"derive_exclude": list(globs)}}

    def test_a_fixture_is_dropped_when_the_subject_says_so(self):
        from kernel.analysis import subject_files
        got = subject_files.keep(
            self.subject(["tests/fixtures/**"]),
            ["app/a.py", "tests/fixtures/secret/bypass/cfg.py", "tests/test_a.py"])
        self.assertEqual(got, ["app/a.py", "tests/test_a.py"])

    def test_a_subject_that_says_nothing_drops_nothing(self):
        from kernel.analysis import subject_files
        got = subject_files.keep({}, ["tests/fixtures/x.py", "app/a.py"])
        self.assertEqual(got, ["app/a.py", "tests/fixtures/x.py"])

    def test_a_fixture_run_is_about_the_broken_code_so_nothing_is_dropped(self):
        # `register`'s harness writes no params. Excluding there would make
        # every red case pass, which is the one thing this must not do.
        from kernel.analysis import subject_files
        self.assertEqual(subject_files.exclusions({"params": {}}), ())

    def test_the_three_glob_spellings_agree_with_files_in_scope(self):
        from kernel.analysis import subject_files
        from kernel.lifecycle import _files_in_scope   # noqa: F401  (same rule)
        for glob in ("tests/fixtures/**", "tests/fixtures/", "tests/fixtures/*"):
            self.assertTrue(subject_files.excluded("tests/fixtures/a.py", [glob]),
                            glob)
        self.assertFalse(subject_files.excluded("tests/test_a.py",
                                                ["tests/fixtures/**"]))


class AKindThatCannotBeAnsweredIsNotInstalled(unittest.TestCase):
    """A permanent exit 4 is worse than a missing rule.

    Measured on a first adoption: two of the twelve claims on the first task
    were `layer-boundary` and `control-plane-budget`, each waiting on a file
    the repo had never declared. Neither could ever be answered; the only way
    past was a signature per kind, and nothing told the adopter that. A rule
    that is absent announces itself in `doctor`. One that blocks every task
    reads as the framework being broken.
    """

    def _src(self, spec, reads=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({
            "scope": {"checker": "scope", "question_template": "q",
                      "staleness": "repo"},
            "dead-wiring": {"checker": "dw", "question_template": "q",
                            "applies_to": "framework"},
            "layer-boundary": spec,
        }))
        # What each checker can read. The third holdback rule is asked against
        # this, so a fixture without it is a fixture where that rule is off.
        (tmp / ".v4" / "checkers.json").write_text(json.dumps({
            "scope": {"path": "checkers/scope.py"},
            "dw": {"path": "checkers/dw.py"},
            "lb": {"path": "checkers/lb.py", "reads": reads or []},
        }))
        return tmp

    SPEC = {"checker": "lb", "question_template": "q",
            "applies_to": "declared", "needs": ".v4/layers.json"}

    def test_held_back_when_the_repo_has_not_declared_it(self):
        from kernel import install
        dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        keep, held = install.installable_kinds(self._src(self.SPEC), dst)
        self.assertNotIn("layer-boundary", keep)
        self.assertIn("layer-boundary", dict(held))
        self.assertIn(".v4/layers.json", dict(held)["layer-boundary"])

    def test_installed_once_the_repo_declares_it(self):
        from kernel import install
        dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        (dst / ".v4").mkdir()
        (dst / ".v4" / "layers.json").write_text('{"layers": {}}')
        keep, held = install.installable_kinds(self._src(self.SPEC), dst)
        self.assertIn("layer-boundary", keep)
        self.assertNotIn("layer-boundary", dict(held))
        # and the bookkeeping fields do not travel into the adopter's registry
        self.assertNotIn("applies_to", keep["layer-boundary"])
        self.assertNotIn("needs", keep["layer-boundary"])

    def test_framework_only_is_still_held_back_either_way(self):
        from kernel import install
        dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        (dst / ".v4").mkdir()
        (dst / ".v4" / "layers.json").write_text('{"layers": {}}')
        keep, held = install.installable_kinds(self._src(self.SPEC), dst)
        self.assertNotIn("dead-wiring", keep)

    def test_no_destination_holds_it_back_rather_than_guessing(self):
        from kernel import install
        keep, held = install.installable_kinds(self._src(self.SPEC))
        self.assertNotIn("layer-boundary", keep)

    def test_a_plain_kind_is_never_held_back(self):
        from kernel import install
        dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        keep, _ = install.installable_kinds(self._src(self.SPEC), dst)
        self.assertIn("scope", keep)

    #: A kind whose checker has nothing to read here.  Nothing is `declared`
    #: about it -- it is installable everywhere except a repo of the wrong
    #: language, which is a question only the repo's own files can answer.
    UNREADABLE = {"checker": "lb", "question_template": "q"}

    def _go_repo(self):
        dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        (dst / "main.go").write_text("package main\n")
        return dst

    def test_a_checker_with_nothing_to_read_here_is_held_back(self):
        from kernel import install
        src = self._src(self.UNREADABLE, reads=["**/*.py"])
        keep, held = install.installable_kinds(src, self._go_repo())
        self.assertNotIn("layer-boundary", keep)

    def test_and_the_held_list_says_so(self):
        """The whole point. This rule used to live in `cmd_install`, which
        popped the kind out of the table *after* `installable_kinds` returned,
        so `held` -- the list an adopter reads to find what they did not get --
        never carried it. A holdback nothing reports is a rule that vanishes."""
        from kernel import install
        src = self._src(self.UNREADABLE, reads=["**/*.py"])
        _keep, held = install.installable_kinds(src, self._go_repo())
        self.assertIn("layer-boundary", dict(held))
        self.assertIn("**/*.py", dict(held)["layer-boundary"])

    def test_it_is_installed_where_its_checker_can_read(self):
        """The control: a rule that held everything back would pass the two
        above."""
        from kernel import install
        dst = self._go_repo()
        (dst / "app.py").write_text("x = 1\n")
        src = self._src(self.UNREADABLE, reads=["**/*.py"])
        keep, held = install.installable_kinds(src, dst)
        self.assertIn("layer-boundary", keep)
        self.assertNotIn("layer-boundary", dict(held))

    def test_the_question_is_asked_of_their_files_not_this_frameworks(self):
        """The second `v4 install` on a Go repo has to answer the same as the
        first.

        `copy_files` put 83 Python files into the tree, and every one of them
        is named in the registry it also wrote -- so asking "does this repo
        contain Python" over the whole tree answers about the framework, and
        every Python-AST checker installs itself into a Go repo on the strength
        of its own source. This is what `repo_own_files` is for; without it the
        holdback works once and never again.
        """
        from kernel import install
        dst = self._go_repo()
        for rel in ("checkers/scope.py", "detectors/fail_closed.py",
                    "hooks/stop_gate.py"):
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            (dst / rel).write_text("x = 1\n")
        (dst / ".v4").mkdir()
        (dst / ".v4" / "checkers.json").write_text(json.dumps(
            {"scope": {"path": "checkers/scope.py"}}))
        (dst / ".v4" / "detectors.json").write_text(json.dumps(
            {"fail_closed.py": {"path": "detectors/fail_closed.py"}}))
        src = self._src(self.UNREADABLE, reads=["**/*.py"])
        keep, held = install.installable_kinds(src, dst)
        self.assertNotIn("layer-boundary", keep)
        self.assertIn("layer-boundary", dict(held))


class TheInstallExemptionCannotHideAnEdit(unittest.TestCase):
    """`kernel_written` decides whether a worker's edit to a checker is visible.

    The record-based half -- a path is exempt only while its bytes still match
    what `v4 install` wrote -- had no test and no fixture: nothing in the suite
    wrote a `.v4/installed.json`, so `_shipped` returned `{}` everywhere and
    execution short-circuited one line in. Replacing the comparison with
    `return f.is_file()` left all 688 green, and with that in place an edited
    `checkers/scope.py` drops out of the changed-file set the `scope` checker
    reads and out of the executed set `test` reads.
    """

    def _repo(self, body="x = 1\n"):
        from kernel import hashing
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / ".v4").mkdir()
        (root / "checkers").mkdir()
        f = root / "checkers" / "thing.py"
        f.write_text(body)
        (root / ".v4" / "installed.json").write_text(json.dumps(
            {"checkers/thing.py": hashing.file_sha(f)}))
        return root, f

    def test_an_untouched_shipped_file_is_exempt(self):
        from kernel import hashing
        root, _ = self._repo()
        self.assertTrue(hashing.kernel_written("checkers/thing.py", root))

    def test_an_edited_shipped_file_is_not_exempt(self):
        from kernel import hashing
        root, f = self._repo()
        f.write_text("x = 2   # weakened by whoever is being judged\n")
        self.assertFalse(
            hashing.kernel_written("checkers/thing.py", root),
            "a checker was edited and stayed exempt, so the edit is invisible "
            "to the two checkers that read the changed-file set")

    def test_a_file_install_never_wrote_is_not_exempt(self):
        from kernel import hashing
        root, _ = self._repo()
        (root / "checkers" / "mine.py").write_text("y = 1\n")
        self.assertFalse(hashing.kernel_written("checkers/mine.py", root))


class AnswersGoStaleWhenTheDecidingCodeChanges(unittest.TestCase):
    """`checker_sha` was the entry file, and the entry file is a CLI.

    20 of 27 checkers parse arguments and hand off to `kernel/analysis/`. So
    "a different program gave that answer" was true of the argument parser and
    false of the judgement. Measured here: a false-positive class in
    `kernel/analysis/dangling_ref.py` was fixed, the verdict went from 42
    findings to none, and no recorded PASS went stale.
    """

    def _tree(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "kernel" / "analysis").mkdir(parents=True)
        (tmp / "kernel" / "__init__.py").write_text("")
        (tmp / "kernel" / "analysis" / "judge.py").write_text("VERDICT = 0\n")
        (tmp / "kernel" / "helper.py").write_text("H = 1\n")
        (tmp / "checkers").mkdir()
        (tmp / "checkers" / "c.py").write_text(
            "from kernel.analysis import judge\nfrom kernel import helper\n")
        return tmp

    def test_editing_the_module_that_decides_changes_the_sha(self):
        from kernel import hashing
        t = self._tree()
        before = hashing.program_sha(t, t / "checkers" / "c.py")
        (t / "kernel" / "analysis" / "judge.py").write_text("VERDICT = 1\n")
        self.assertNotEqual(hashing.program_sha(t, t / "checkers" / "c.py"), before)

    def test_a_transitive_import_counts_too(self):
        from kernel import hashing
        t = self._tree()
        (t / "kernel" / "analysis" / "judge.py").write_text(
            "from kernel import helper\nVERDICT = helper.H\n")
        before = hashing.program_sha(t, t / "checkers" / "c.py")
        (t / "kernel" / "helper.py").write_text("H = 99\n")
        self.assertNotEqual(hashing.program_sha(t, t / "checkers" / "c.py"), before)

    def test_a_relative_import_counts_too(self):
        """`from . import x` reached the judgement and was not hashed.

        Every other test in this class writes absolute imports, which is why
        this survived: the walker required `node.module and not node.level`, and
        a relative import fails both halves -- `from . import x` has no module
        and `from .x import y` has a level. Measured on this repo before the
        fix: 20 of 29 registered checkers missed at least one module that
        decides their verdict, including `kernel/analysis/pysource.py`, and
        `kernel/analysis/dangling_ref.py`, the module `program_sha`'s own
        docstring cites as the failure it exists to prevent.
        """
        from kernel import hashing
        t = self._tree()
        (t / "kernel" / "analysis" / "__init__.py").write_text("")
        (t / "kernel" / "analysis" / "judge.py").write_text(
            "from . import inner\nVERDICT = inner.V\n")
        (t / "kernel" / "analysis" / "inner.py").write_text("V = 0\n")
        before = hashing.program_sha(t, t / "checkers" / "c.py")
        (t / "kernel" / "analysis" / "inner.py").write_text("V = 1\n")
        self.assertNotEqual(
            hashing.program_sha(t, t / "checkers" / "c.py"), before,
            "a module reached by `from . import` decided the verdict and "
            "changing it left every prior PASS looking fresh")

    def test_a_relative_import_one_package_up_counts_too(self):
        from kernel import hashing
        t = self._tree()
        (t / "kernel" / "analysis" / "__init__.py").write_text("")
        (t / "kernel" / "analysis" / "judge.py").write_text(
            "from .. import helper\nVERDICT = helper.H\n")
        before = hashing.program_sha(t, t / "checkers" / "c.py")
        (t / "kernel" / "helper.py").write_text("H = 42\n")
        self.assertNotEqual(hashing.program_sha(t, t / "checkers" / "c.py"), before)

    def test_an_unrelated_file_in_the_repo_does_not(self):
        from kernel import hashing
        t = self._tree()
        before = hashing.program_sha(t, t / "checkers" / "c.py")
        (t / "kernel" / "unused.py").write_text("Z = 1\n")
        self.assertEqual(hashing.program_sha(t, t / "checkers" / "c.py"), before)

    def test_a_stdlib_import_is_not_followed(self):
        from kernel import hashing
        t = self._tree()
        before = hashing.program_sha(t, t / "checkers" / "c.py")
        (t / "checkers" / "c.py").write_text(
            "import json\nimport os\nfrom kernel.analysis import judge\n"
            "from kernel import helper\n")
        after = hashing.program_sha(t, t / "checkers" / "c.py")
        self.assertNotEqual(after, before)          # the entry file itself moved
        # but adding another stdlib import to the same file is the only reason
        (t / "checkers" / "c.py").write_text(
            "import json\nimport os\nfrom kernel.analysis import judge\n"
            "from kernel import helper\n")
        self.assertEqual(hashing.program_sha(t, t / "checkers" / "c.py"), after)

    def test_an_import_cycle_terminates(self):
        from kernel import hashing
        t = self._tree()
        (t / "kernel" / "analysis" / "judge.py").write_text(
            "from kernel import helper\n")
        (t / "kernel" / "helper.py").write_text(
            "from kernel.analysis import judge\n")
        self.assertTrue(hashing.program_sha(t, t / "checkers" / "c.py"))

    def test_the_tamper_check_still_reads_the_entry_file_alone(self):
        # `checkers.json` records the entry file's bytes and `runner` refuses to
        # execute anything else. Widening that to the closure would mean a repo
        # could not fix a shared helper without re-registering every checker.
        from kernel import hashing
        t = self._tree()
        entry = t / "checkers" / "c.py"
        registered = hashing.file_sha(entry)
        (t / "kernel" / "analysis" / "judge.py").write_text("VERDICT = 2\n")
        self.assertEqual(hashing.file_sha(entry), registered)


class PaddingACopyIsStillACopy(unittest.TestCase):
    """The overlap test was defeated by appending.

    Measured: pasting a rule back verbatim was refused at overlap 1.0, and nine
    words of "呢個好重要要小心處理" brought it to 0.78 and through. Real
    sentences written against this repo's own rules carried 12-21 words the rule
    did not; padded copies carried 4-6.
    """

    RULE = ("一個洩漏咗嘅憑證,唔係刪走嗰行就修好 —— 要 rotate,要清 history。")
    RULE2 = ("`.gitignore` 唔可以代替 secret management。checker 只睇改動過嘅檔,"
             "睇唔到 working tree 入面已經 gitignore 咗嘅明文。")

    KINDS = {"secret": {"question_template": "q", "checker": "s",
                        "staleness": "repo", "engagement": True,
                        "rule": [{"text": RULE, "source": "a"},
                                 {"text": RULE2, "source": "b"}]}}

    def _cfg(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "protected_paths": [".v4/**"],
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8,
                            "ship_rederive_max": 3, "widen_warn_pct": 5}}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(self.KINDS))
        return config.RepoConfig(tmp)

    def _row(self):
        return {"kind": "secret", "file": "", "symbol": "", "id": "c1"}

    def _judge(self, text):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        # Both tables, because the duplicate check reads a sentence's claim to
        # find out which finding it was about. A stub with only `event` was
        # answering a question the real ledger answers with a join.
        conn.execute("CREATE TABLE event (id INTEGER PRIMARY KEY, kind TEXT, "
                     "payload TEXT, claim_id TEXT)")
        conn.execute("CREATE TABLE claim (id TEXT PRIMARY KEY, kind TEXT, "
                     "file TEXT, symbol TEXT)")
        return engagement.judge(conn, self._cfg(), claim_row=self._row(),
                                sentence=text)

    def test_a_verbatim_copy_is_refused(self):
        ok, why = self._judge(self.RULE)
        self.assertFalse(ok)

    def test_a_copy_with_nine_words_of_filler_is_refused(self):
        ok, why = self._judge(self.RULE + "呢個好重要要小心處理。")
        self.assertFalse(ok, why)
        self.assertIn("came back", why)

    def test_a_copy_with_a_lot_of_filler_is_still_refused(self):
        ok, why = self._judge(self.RULE + "呢個好重要要小心處理，我會跟返足。")
        self.assertFalse(ok, why)

    def test_a_sentence_about_the_code_passes(self):
        ok, why = self._judge(
            "個 token 由 caller 傳入 fetch()，冇喺 app/client.py 寫死；"
            "tests/test_client.py:12 傳咗一個假嘅落去，個值本身冇講明佢係假。")
        self.assertTrue(ok, why)

    def test_a_short_sentence_naming_real_things_passes(self):
        ok, why = self._judge(
            "fetch() 用 raise RuntimeError('fetch failed') from exc，"
            "個 requests exception 連住 URL 仲喺 __cause__ 度。")
        self.assertTrue(ok, why)

    def test_no_amount_of_padding_gets_a_copy_through(self):
        pad = "呢個好重要要小心處理，我會跟返足，多謝晒你嘅提醒同埋指教。"
        for n in (1, 2, 4, 8):
            ok, why = self._judge(self.RULE + pad * n)
            self.assertFalse(ok, f"{n} copies of padding: {why}")

    def test_it_reads_every_rule_on_screen_not_just_the_first(self):
        ok, why = self._judge(self.RULE2)
        self.assertFalse(ok, why)

    def test_recited_is_containment_not_jaccard(self):
        # The whole point: a longer sentence does not dilute it.
        r = [{"text": "aaa bbb ccc"}]
        self.assertEqual(engagement.recited("aaa bbb ccc", r), 1.0)
        self.assertEqual(engagement.recited("aaa bbb ccc ddd eee fff", r), 1.0)
        self.assertAlmostEqual(engagement.recited("aaa zzz", r), 1 / 3)
        self.assertEqual(engagement.recited("zzz", r), 0.0)
        self.assertEqual(engagement.recited("aaa", []), 0.0)


class TheRulePrintDoesNotContradictItself(unittest.TestCase):
    """`for ... else` runs whenever the loop was not broken out of.

    Here that is always, so every claim carrying a rule printed the rule and
    then, one line underneath, told the worker this kind carries no rule. The
    one screen this layer has, saying two opposite things at once.
    """

    def _repo(self, rule):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "protected_paths": [".v4/**"],
             "thresholds": {"min_chars": 40, "dup_threshold": 0.8,
                            "ship_rederive_max": 3, "widen_warn_pct": 5}}))
        kind = {"question_template": "q?", "checker": "c", "staleness": "repo",
                "engagement": True}
        if rule:
            kind["rule"] = rule
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps({"k": kind}))
        (tmp / "a.py").write_text("X = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, capture_output=True)
        return tmp

    def _show(self, tmp):
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t", request="r", scope_globs="[]",
                      base_commit="0", created_at="n")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="k",
                      question="q?", file="a.py", symbol="", variant="",
                      subject_refs="[]", checker="c", detector="",
                      detector_sha="", origin="detector", created_at="n")
        conn.commit()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.cmd_engage(argparse.Namespace(
                repo=str(tmp), acceptance=".v4/acceptance.json", claim="c1",
                text=None, why=None))
        return out.getvalue()

    GAP = "carries no rule"

    def test_a_kind_with_a_rule_never_says_it_has_none(self):
        text = self._show(self._repo(
            [{"text": "一個預期值移動咗而佢判緊嗰段 code 冇動。", "source": "CLAUDE.md"}]))
        self.assertIn("一個預期值移動咗", text)
        self.assertNotIn(self.GAP, text)

    def test_several_rules_are_numbered_and_still_no_contradiction(self):
        text = self._show(self._repo(
            [{"text": "第一條規則講嘅嘢。", "source": "a"},
             {"text": "第二條規則講嘅嘢。", "source": "b"}]))
        self.assertIn("rule 1", text)
        self.assertIn("rule 2", text)
        self.assertNotIn(self.GAP, text)

    def test_a_kind_with_no_rule_still_says_so(self):
        text = self._show(self._repo(None))
        self.assertIn(self.GAP, text)


class ACheckerWithNothingToReadHasNoVerdict(unittest.TestCase):
    """Measured on a Go repo with four planted defects.

    Of 27 checkers, 15 returned exit 4 -- "I cannot answer this" -- and 8
    returned 0 having parsed no Go at all. `dependency_audit` reported
    `PASS: no package manifest declares a dependency` about a `go.mod` with an
    unpinned require and no `go.sum`.

    Every one of the 8 was written by an author who did not think about it, and
    the 15 by authors who did. A rule that holds only when each of 27 authors
    remembers it is a tally of who was careful, not a rule. So the checker stops
    deciding: it declares what it reads, and one that has nothing to read is not
    run.
    """

    def test_a_python_checker_reads_nothing_in_a_go_tree(self):
        from kernel.analysis import subject_files
        go = ["go.mod", "internal/client.go", "cmd/main.go"]
        self.assertFalse(subject_files.readable(go, ["**/*.py"]))

    def test_and_does_once_one_python_file_appears(self):
        from kernel.analysis import subject_files
        self.assertTrue(subject_files.readable(
            ["go.mod", "scripts/build.py"], ["**/*.py"]))

    def test_language_independent_is_a_statement_not_a_blank(self):
        from kernel.analysis import subject_files
        self.assertTrue(subject_files.readable(["go.mod"], ["**"]))
        # and a blank still runs -- the registry gate is what refuses it, so
        # the two are not confused here
        self.assertTrue(subject_files.readable(["go.mod"], []))

    def test_a_manifest_list_matches_only_manifests(self):
        from kernel.analysis import subject_files
        reads = ["**/requirements*.txt", "**/package.json"]
        self.assertFalse(subject_files.readable(["go.mod", "main.go"], reads))
        self.assertTrue(subject_files.readable(["web/package.json"], reads))
        self.assertTrue(subject_files.readable(["requirements-dev.txt"], reads))

    def test_a_dotted_registry_glob_matches_the_v4_directory(self):
        from kernel.analysis import subject_files
        self.assertTrue(subject_files.readable(
            [".v4/checkers.json", "main.go"], [".v4/*.json"]))
        self.assertFalse(subject_files.readable(["main.go"], [".v4/*.json"]))

    def test_every_registered_checker_declares_what_it_reads(self):
        # The registry gate says so too; this fails in the repo rather than in
        # one claim's attempt row, which is where somebody reads it.
        reg = json.loads((REPO / ".v4" / "checkers.json").read_text())
        missing = sorted(c for c, e in reg.items() if not e.get("reads"))
        self.assertEqual(missing, [])


class NoKindStopsYouAndThenSaysNothing(unittest.TestCase):
    """A gate that refuses and names nothing is a request for characters.

    `lint` carried `engagement: true` and zero rules, so every task hit a
    checkpoint whose whole screen was the framework saying "this kind carries no
    rule; that is a gap, not a design". Nine other kinds already say the honest
    thing instead -- `engagement: false`, because the checker answers it.
    """

    def test_every_engaged_kind_carries_a_rule_with_text_in_it(self):
        kinds = json.loads((REPO / ".v4" / "claim_kinds.json").read_text())
        blank = []
        for name, spec in sorted(kinds.items()):
            if not spec.get("engagement"):
                continue
            rules = spec.get("rule") or []
            if isinstance(rules, dict):
                rules = [rules]
            if not any((r.get("text") or "").strip() for r in rules):
                blank.append(name)
        self.assertEqual(blank, [],
                         "these stop the worker and then name nothing")

    def test_a_kind_that_is_not_engaged_says_why(self):
        # The alternative to a rule is a stated reason, not silence. Otherwise
        # `engagement: false` becomes the quiet way to delete a checkpoint.
        #
        # `not spec.get("engagement_why")` used to be part of the `continue`,
        # which skipped exactly the rows this exists for: it measured the length
        # of reasons that already existed and said nothing about the ones that
        # did not. Measured when that came out: 8 of the 11 non-engaged kinds
        # carried none, `cmd_engage` printed "reason: none recorded -- that is a
        # gap, not a design" for every one of them, and SPEC §9 said the
        # opposite ("11 kinds carry it today"). The eight were written; the spec
        # is true now.
        kinds = json.loads((REPO / ".v4" / "claim_kinds.json").read_text())
        silent = []
        for name, spec in sorted(kinds.items()):
            if spec.get("engagement"):
                continue
            why = (spec.get("engagement_why") or "").strip()
            if not why:
                silent.append(name)
                continue
            self.assertGreater(len(why), 40, name)
        self.assertEqual(silent, [],
                         "`engagement: false` with no `engagement_why` is a "
                         "checkpoint removed without anybody saying so")


class ATraceFileThatCannotBeReadIsNotACrash(unittest.TestCase):
    """`executed_files` reads a file written by an `atexit` hook in another
    process. A suite that forks workers, or one killed before `atexit` runs,
    leaves it empty or half-written, and `json.loads` on that raised straight out
    of the kernel.

    `None` is already this function's word for "I could not tell what ran", and
    its one caller handles it. A crash is not the same thing and is not handled.
    """

    def _in_a_repo(self, command):
        import tempfile
        from kernel.redgreen import executed_files
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return executed_files(root, command, timeout=60)

    def test_a_child_that_outlives_the_parent_does_not_wipe_the_trace(self):
        """`PYTHONPATH` reaches every Python child a test command starts.

        With one shared trace file the last writer won, and the last writer is
        not the test run: `multiprocessing.resource_tracker` is spawned by
        anything using a semaphore or shared memory and outlives its parent.

        Measured against a real 5,455-test suite: the trace came back holding
        one file and none of the repo's own, so `test` reported "the suite
        passed and executed none of the changed file(s)" about a file its own
        tests import and call.
        """
        import tempfile
        from kernel.redgreen import executed_files
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        (root / "big.py").write_text("def a():\n    return 1\n")
        (root / "run.py").write_text(
            "import big, subprocess, sys\n"
            "big.a()\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1.5)'])\n")
        code, ran, _ = executed_files(root, f"{sys.executable} run.py", timeout=60)
        self.assertEqual(code, 0)
        self.assertIn("big.py", ran or set(),
                      "a later-exiting child must not erase what the run did")

    def test_a_half_written_trace_reads_as_cannot_tell(self):
        # What a process killed mid-dump leaves behind.
        code, ran, _ = self._in_a_repo(
            'printf %s "[{\\"f\\":" > "$V4_TRACE_OUT/99.json"')
        self.assertEqual(code, 0)
        self.assertIsNone(ran, "a trace that will not parse means 'cannot tell'")

    def test_an_empty_trace_reads_as_cannot_tell(self):
        code, ran, _ = self._in_a_repo(': > "$V4_TRACE_OUT/99.json"')
        self.assertEqual(code, 0)
        self.assertIsNone(ran)

    def test_a_good_trace_still_reads(self):
        code, ran, _ = self._in_a_repo(
            'printf %s \'["a.py"]\' > "$V4_TRACE_OUT/99.json"')
        self.assertEqual(code, 0)
        self.assertEqual(ran, {"a.py"})

    def test_a_python_run_that_touched_nothing_is_an_answer(self):
        """Empty is a finding; absent is "I cannot tell". They are not the same.

        A suite that ran none of the repo's files is exactly what `test` exists
        to report. A command that is not Python leaves no trace file at all, and
        that one is unanswerable. Having `_dump` skip the empty case collapsed
        both into `None` and the checker's own bypass fixture stopped failing.
        """
        import tempfile
        from kernel.redgreen import executed_files
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        code, ran, _ = executed_files(root, f"{sys.executable} -c pass", timeout=60)
        self.assertEqual(code, 0)
        self.assertEqual(ran, set(), "a Python run that touched nothing says so")

    def test_a_command_that_is_not_python_cannot_tell(self):
        import tempfile
        from kernel.redgreen import executed_files
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        code, ran, _ = executed_files(root, "true", timeout=60)
        self.assertEqual(code, 0)
        self.assertIsNone(ran, "nothing traced at all is not an empty answer")

    def test_one_unreadable_file_does_not_lose_the_others(self):
        """A killed worker must not cost the run everything the rest recorded."""
        code, ran, _ = self._in_a_repo(
            'printf %s \'["a.py"]\' > "$V4_TRACE_OUT/1.json"; '
            'printf %s "[{" > "$V4_TRACE_OUT/2.json"')
        self.assertEqual(code, 0)
        self.assertEqual(ran, {"a.py"})


class AFixHasToBeAbleToReachAnAdopter(unittest.TestCase):
    """`install` never overwrote, so no fix ever arrived.

    Never overwriting is right about a file the repo has edited: silently
    restoring the original answers that repo's claims with a program its author
    did not write. It is wrong about every other file, and the consequence is
    that a whole day of fixes sat undeliverable behind one `if d.exists()`.

    Measured: `bash_guard` in an adopting repo could not import `kernel`, so the
    one hook whose reason for existing is `sed -i .v4/config.json` allowed that
    command silently. Fixing it in the framework changed nothing there.
    """

    def _pair(self):
        src = Path(tempfile.mkdtemp()); dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, src, ignore_errors=True)
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        for d in ("checkers", "detectors", "hooks", ".v4/lenses", ".claude"):
            (src / d).mkdir(parents=True, exist_ok=True)
        (src / "checkers" / "c.py").write_text("v1\n")
        (src / "hooks" / "h.py").write_text("v1\n")
        (src / ".v4" / "checkers.json").write_text(json.dumps(
            {"c": {"path": "checkers/c.py", "fixtures": "", "kinds": ["k"],
                   "sha256": "", "reads": ["**"]}}))
        (src / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"k": {"checker": "c", "question_template": "q"}}))
        (src / ".v4" / "detectors.json").write_text("{}")
        (src / ".claude" / "settings.template.json").write_text("{}")
        return src, dst

    KINDS = {"k": {"checker": "c", "question_template": "q"}}

    def test_first_install_writes_and_records(self):
        from kernel import install
        src, dst = self._pair()
        out = dict(install.copy_files(src, dst, self.KINDS))
        self.assertEqual(out["checkers/c.py"], "written")
        self.assertTrue((dst / install.MANIFEST).is_file())

    def test_an_untouched_file_is_updated(self):
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, self.KINDS)
        (src / "checkers" / "c.py").write_text("v2\n")
        out = dict(install.copy_files(src, dst, self.KINDS))
        self.assertEqual(out["checkers/c.py"], "updated")
        self.assertEqual((dst / "checkers" / "c.py").read_text(), "v2\n")

    def test_a_kind_held_back_does_not_lose_its_checker_s_record(self):
        """`now` is built only from what this run decided about.

        A kind held back -- because the repo has no file its checker reads, or
        because it was refused -- is not among them, and its checker is still on
        disk from the last install. Rebuilding the manifest from scratch dropped
        the record, and `kernel_written` then said a person had written it.

        Measured on the real adopter: `scope` reported
        `checkers/external_write.py` as the worker changing what judges it, about
        a file byte-identical to the one this framework had shipped.
        """
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, self.KINDS)
        shipped = json.loads((dst / install.MANIFEST).read_text())
        self.assertIn("checkers/c.py", shipped)

        # This run decides about no kinds at all -- the held-back case.
        install.copy_files(src, dst, {})
        kept = json.loads((dst / install.MANIFEST).read_text())
        self.assertEqual(kept.get("checkers/c.py"), shipped["checkers/c.py"],
                         "a file this run did not touch kept its record")

    def test_a_file_edited_since_shipping_loses_its_record(self):
        """The carry-forward may not resurrect a record for an edited file."""
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, self.KINDS)
        (dst / "checkers" / "c.py").write_text("mine\n")
        install.copy_files(src, dst, {})
        kept = json.loads((dst / install.MANIFEST).read_text())
        self.assertNotEqual(
            kept.get("checkers/c.py"),
            hashing.file_sha(src / "checkers" / "c.py"),
            "an edit must not be recorded as something the framework shipped")

    def test_a_file_the_repo_edited_is_left_alone_and_named(self):
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, self.KINDS)
        (dst / "checkers" / "c.py").write_text("mine\n")
        (src / "checkers" / "c.py").write_text("v2\n")
        out = dict(install.copy_files(src, dst, self.KINDS))
        self.assertEqual(out["checkers/c.py"], "yours")
        self.assertEqual((dst / "checkers" / "c.py").read_text(), "mine\n")

    def test_an_edit_still_blocks_the_update_on_the_run_after(self):
        # Not just once: the manifest must keep recording the file as theirs.
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, self.KINDS)
        (dst / "checkers" / "c.py").write_text("mine\n")
        install.copy_files(src, dst, self.KINDS)
        (src / "checkers" / "c.py").write_text("v3\n")
        out = dict(install.copy_files(src, dst, self.KINDS))
        self.assertEqual(out["checkers/c.py"], "yours")

    def test_a_repo_with_no_manifest_keeps_everything(self):
        # An adopter installed before the manifest existed. Nothing there can be
        # shown to be untouched, so nothing is overwritten.
        from kernel import install
        src, dst = self._pair()
        install.copy_files(src, dst, self.KINDS)
        (dst / install.MANIFEST).unlink()
        (src / "checkers" / "c.py").write_text("v2\n")
        out = dict(install.copy_files(src, dst, self.KINDS))
        self.assertEqual(out["checkers/c.py"], "yours")
        self.assertEqual((dst / "checkers" / "c.py").read_text(), "v1\n")


class EveryThingCloningItselfIntoAnAdopterIsNamedInTheTable(unittest.TestCase):
    """SPEC §8.8 answers "what does an adopter get its own copy of".

    It was missing two of them -- `.v4/fixtures/` and `.v4/lenses/`, both of
    which `copy_files` writes and `hashing.SHIPPED_DIRS` already knows are
    clones. A reader deciding whether to edit a lens in their own repo, or
    whether a fixture change reaches them, got the wrong answer from the one
    table written to give it.

    Asked of `copy_files` rather than of a list: a list beside a loop drifts
    from the loop, which is how the row went missing. This runs the real thing
    against the real framework and reads the destinations off what it did.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def _cloned_roots(self):
        from kernel import install
        dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        (dst / ".v4").mkdir(parents=True)
        kinds, _held = install.installable_kinds(self.ROOT)
        roots = set()
        for rel, _how in install.copy_files(self.ROOT, dst, kinds):
            parts = Path(rel).parts
            roots.add("/".join(parts[:2]) if parts[0] == ".v4" else parts[0])
        return roots

    def test_the_table_names_every_one_of_them(self):
        spec = (self.ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
        start = spec.index("### 8.8")
        section = spec[start:spec.index("\n## ", start)]
        missing = [r for r in sorted(self._cloned_roots()) if r not in section]
        self.assertEqual(missing, [], "\n".join(
            ["`v4 install` clones these into an adopter and SPEC §8.8 does not "
             "say so:"] + missing))

    def test_there_was_something_to_look_for(self):
        """A `copy_files` that copied nothing would pass the test above."""
        roots = self._cloned_roots()
        self.assertIn(".v4/fixtures", roots)
        self.assertIn(".v4/lenses", roots)
        self.assertIn("checkers", roots)


class TheTwoHalvesOfTheWriteGuard(unittest.TestCase):
    """`bash_guard` and `write_block` are 同一個問題嘅兩半, and only one had both.

    Measured before this, in this checkout: `sed -i .github/monitor/PROMPT.md`
    was denied by `bash_guard`, while `Write` payloads for the same path and
    for `.v4/config.json` -- the file naming the sole test oracle -- returned
    `{}` from `write_block`. `checkers/scope.py:109` names that escape out loud
    ("write it with no task open, and commit that separately"), and
    `ledger.ENDED_TASKS_SQL` unions `repo-review` into the ended set, so a
    review or monitor session is always in the state where it works.

    The asymmetry that stays is deliberate: under a task this is a scope
    question, because `bash_guard`'s own refusal offers "make the change
    through Write/Edit where the scope hook can see it" as the way through, and
    denying on both routes would leave this framework's own `checkers/`
    unwritable by any means.
    """

    FW = REPO / "hooks"

    def _repo(self, task=None, scope=("**",)):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / "checkers").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / ".v4" / "checkers.json").write_text("{}")
        (tmp / "app.py").write_text("x = 1\n")
        (tmp / "checkers" / "c.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)
        if task:
            conn = ledger.connect(tmp)
            lifecycle.open_task(conn, config.RepoConfig(tmp), task_id=task,
                                request="r", scope_globs=list(scope))
            conn.close()
        return tmp

    def _env(self, root, task=None):
        e = {k: v for k, v in os.environ.items() if k != "V4_HOME"}
        e["V4_REPO"] = str(root)
        e.pop("V4_TASK", None)
        if task:
            e["V4_TASK"] = task
        return e

    def _write(self, root, rel, task=None):
        return subprocess.run(
            [sys.executable, str(self.FW / "write_block.py")],
            input=json.dumps({"tool_name": "Write",
                              "tool_input": {"file_path": rel, "content": "x"},
                              "cwd": str(root)}),
            capture_output=True, text=True, cwd=str(root),
            env=self._env(root, task)).stdout

    def _bash(self, root, cmd, task=None):
        return subprocess.run(
            [sys.executable, str(self.FW / "bash_guard.py")],
            input=json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": cmd}}),
            capture_output=True, text=True, cwd=str(root),
            env=self._env(root, task)).stdout

    def _events(self, root):
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                                cwd=root, capture_output=True, text=True).stdout.strip()
        db = Path(common)
        if not db.is_absolute():
            db = (root / db).resolve()
        conn = sqlite3.connect(db / "v4" / "ledger.db")
        self.addCleanup(conn.close)
        return [json.loads(r[0]) for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'hook_seen' ORDER BY id")]

    def test_no_task_open_still_refuses_a_protected_write(self):
        root = self._repo()
        for rel in (".v4/config.json", "checkers/c.py"):
            self.assertIn("deny", self._write(root, rel), rel)

    def test_no_task_open_leaves_an_ordinary_write_alone(self):
        root = self._repo()
        self.assertNotIn("deny", self._write(root, "app.py"))

    def test_under_a_task_it_is_a_scope_question(self):
        """The route `bash_guard` tells the worker to use has to stay open."""
        root = self._repo(task="t-1", scope=("**",))
        self.assertNotIn("deny", self._write(root, "checkers/c.py", task="t-1"))

    def test_bash_guard_records_what_it_decided(self):
        """It wrote no mark on any path, so a deny that stopped a real command
        was unrecoverable and `ship` counted only the sibling's marks."""
        root = self._repo(task="t-1")
        self.assertIn("deny", self._bash(root, "sed -i '' s/a/b/ .v4/config.json",
                                         task="t-1"))
        self._bash(root, "echo hi", task="t-1")
        marks = self._events(root)
        self.assertEqual([m["allowed"] for m in marks], [False, True])
        self.assertEqual(marks[0]["reason"], "protected path")

    def test_the_basis_stays_a_value_it_is_compared_against(self):
        """`unengaged` matches `json_extract(payload, '$.basis') = 'cleared'`
        and `main` used to concatenate prose onto it, so the engagement gate
        was never marked spent in the case `open_task` calls ordinary."""
        root = self._repo(task="t-1")
        self._write(root, "app.py", task="t-1")
        basis = self._events(root)[0].get("basis", "")
        self.assertNotIn("(", basis, "prose belongs in `note`, not in the enum")
        self.assertIn(basis, {"unreadable", "spent", "no-claims", "cleared",
                              "owed", "protected", "two tasks open"})

    def test_a_mark_that_cannot_be_written_says_so(self):
        """`except Exception: pass` produced the same output as a hook nobody
        installed, in the writer of the record that exists to tell them apart."""
        sys.path.insert(0, str(self.FW))
        import _framework
        why = _framework.record_seen(Path(tempfile.mkdtemp()), None, "x.py")
        self.assertTrue(why, "a failed write has to name its reason")


class TheHooksFindTheFrameworkTheSameWay(unittest.TestCase):
    """One resolver, and it answers with `V4_HOME` unset and no `.v4/home`.

    Three hooks carried three copies of "where does `kernel` live". Only
    `bash_guard`'s checked `kernel/` beside `hooks/`, so in this repo -- where
    `V4_HOME` is set nowhere and `.v4/home` is written only into an adopter --
    `write_block` and `stop_gate` resolved to `None` on every invocation and
    stood down without a word: every `Write` allowed unchecked, no `hook_seen`
    row since 2026-08-10, and `v4 ship` printing DEGRADED whether the hook had
    fired or not.

    The environment here is that state exactly: neither source of the answer
    exists, so what is being asserted is the branch that needs no file.
    """

    HOOK = REPO / "hooks" / "write_block.py"

    def _adopter(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _git_repo(tmp)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / ".v4" / "checkers.json").write_text("{}")
        (tmp / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, check=True)
        subprocess.run(["git", "commit", "-qm", "i"], cwd=tmp, check=True)
        conn = ledger.connect(tmp)
        lifecycle.open_task(conn, config.RepoConfig(tmp), task_id="t-1",
                            request="r", scope_globs=["app/**"])
        conn.close()
        self.assertFalse((tmp / ".v4" / "home").exists(), "the state under test")
        return tmp

    def _write(self, root, rel):
        env = {k: v for k, v in os.environ.items() if k != "V4_HOME"}
        env.update(V4_TASK="t-1", V4_REPO=str(root))
        return subprocess.run(
            [sys.executable, str(self.HOOK)],
            input=json.dumps({"tool_name": "Write",
                              "tool_input": {"file_path": rel, "content": "x"},
                              "cwd": str(root)}),
            capture_output=True, text=True, cwd=str(root), env=env)

    def _marks(self, root):
        common = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                                cwd=root, capture_output=True, text=True).stdout.strip()
        db = Path(common)
        if not db.is_absolute():
            db = (root / db).resolve()
        conn = sqlite3.connect(db / "v4" / "ledger.db")
        self.addCleanup(conn.close)
        return conn.execute(
            "SELECT count(*) FROM event WHERE kind = 'hook_seen'").fetchone()[0]

    def test_a_write_outside_the_scope_is_refused(self):
        root = self._adopter()
        self.assertIn("deny", self._write(root, "other/y.py").stdout)

    def test_a_write_inside_the_scope_is_allowed(self):
        root = self._adopter()
        self.assertNotIn("deny", self._write(root, "app/x.py").stdout)

    def test_the_hook_leaves_a_mark_either_way(self):
        """`ship` reports DEGRADED off these rows, so a working hook that
        records nothing reads exactly like a hook nobody installed."""
        root = self._adopter()
        self._write(root, "app/x.py")
        self._write(root, "other/y.py")
        self.assertEqual(self._marks(root), 2)


class AHookThatCannotCheckSaysSo(unittest.TestCase):
    """A dead guard and a clean command must not look the same."""

    HOOK = REPO / "hooks" / "bash_guard.py"

    def _run(self, cmd, cwd, env=None):
        e = {"PATH": "/usr/bin:/bin", "V4_REPO": "."}
        e.update(env or {})
        return subprocess.run(
            [sys.executable, str(self.HOOK)],
            input=json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": cmd}}),
            capture_output=True, text=True, cwd=str(cwd), env=e)

    def test_it_denies_the_command_it_exists_for(self):
        r = self._run('sed -i "" s/a/b/ .v4/config.json', REPO)
        self.assertIn("deny", r.stdout)

    def test_it_allows_an_ordinary_command(self):
        r = self._run("echo hi > /tmp/v4-hook-test.txt", REPO)
        self.assertNotIn("deny", r.stdout)

    def test_it_names_the_reason_when_it_cannot_evaluate(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "hooks").mkdir()
        shutil.copy2(self.HOOK, tmp / "hooks" / "bash_guard.py")
        r = subprocess.run(
            [sys.executable, str(tmp / "hooks" / "bash_guard.py")],
            input=json.dumps({"tool_name": "Bash",
                              "tool_input": {"command": "sed -i x .v4/config.json"}}),
            capture_output=True, text=True, cwd=str(tmp),
            env={"PATH": "/usr/bin:/bin", "V4_REPO": "."})
        self.assertNotIn("deny", r.stdout)          # still allows
        self.assertIn("allowed without checking", r.stderr)   # and says so


class TheHookSaysWhatItDecided(unittest.TestCase):
    """"The hook ran" and "the hook let it through" were the same record.

    And the second cannot be recovered afterwards: `current_scope` grows with
    every widen, so replaying a refusal against the final scope shows it as
    allowed. Measured on one adopter: 147 marks and no way to say whether one of
    them was a refusal.
    """

    HOOK = REPO / "hooks" / "write_block.py"

    def _repo(self, kinds=None, claims=(), engaged=()):
        from kernel import ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / "app").mkdir()
        (tmp / "app" / "m.py").write_text("X = 1\n")
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text("{}")
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(kinds or {}))
        (tmp / ".v4" / "home").write_text(str(REPO))
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t", request="do a thing",
                      scope_globs=["app/**"], base_commit="x", created_at="2026")
        for cid, kind in claims:
            ledger.insert(conn, "claim", id=cid, task_id="t", kind=kind,
                          question="q", subject_refs=json.dumps([]), checker=kind,
                          origin="derive", file="app/m.py", created_at="2026")
        for cid in engaged:
            ledger.insert(conn, "event", task_id="t", claim_id=cid,
                          kind="engagement", actor="worker",
                          payload={"verdict": "accepted", "sentence": "…"},
                          created_at="2026")
        conn.close()
        return tmp

    def _write(self, tmp, rel="app/m.py"):
        r = subprocess.run(
            [sys.executable, str(self.HOOK)],
            input=json.dumps({"tool_name": "Edit",
                              "tool_input": {"file_path": str(tmp / rel)}}),
            capture_output=True, text=True,
            env=dict(__import__("os").environ,
                     V4_REPO=str(tmp), V4_TASK="t"), timeout=60)
        return json.loads(r.stdout or "{}")

    def _marks(self, tmp):
        from kernel import ledger
        conn = ledger.connect(tmp)
        try:
            return [json.loads(r[0]) for r in conn.execute(
                "SELECT payload FROM event WHERE kind = 'hook_seen' ORDER BY id")]
        finally:
            conn.close()

    def test_an_allowed_write_is_recorded_as_allowed(self):
        tmp = self._repo()
        self.assertEqual(self._write(tmp), {})
        self.assertEqual([m["allowed"] for m in self._marks(tmp)], [True])

    def test_a_refused_write_is_recorded_as_refused_with_the_reason(self):
        tmp = self._repo()
        out = self._write(tmp, "other/x.py")
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "deny")
        marks = self._marks(tmp)
        self.assertEqual([m["allowed"] for m in marks], [False])
        self.assertEqual(marks[0]["reason"], "outside scope")


class WritingIsRefusedUntilTheSentenceIsWritten(unittest.TestCase):
    """The rule says "Refused before the work"; the gate ran after it.

    Measured over six tasks, writes before the first sentence: 14, 19, 9, 29,
    15, 22. Two of those wrote every file they were ever going to write before
    the first sentence, so engagement could not have changed anything in them.
    Six claims failed their first check with a sentence already on record
    arguing the code was right.
    """

    KINDS = {"secret": {"engagement": [{"text": "r"}]},
             "lint": {}}

    def setUp(self):
        self.h = TheHookSaysWhatItDecided()
        self.h.addCleanup = self.addCleanup

    def test_a_claim_that_asks_for_a_sentence_blocks_the_first_write(self):
        tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")])
        out = self.h._write(tmp)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("ask for a sentence", out["hookSpecificOutput"]
                      ["permissionDecisionReason"])
        self.assertIn("c1", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_kind_that_asks_for_no_sentence_blocks_nothing(self):
        tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "lint")])
        self.assertEqual(self.h._write(tmp), {})

    def test_the_sentence_opens_the_gate(self):
        tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")],
                           engaged=["c1"])
        self.assertEqual(self.h._write(tmp), {})

    def test_a_refused_sentence_does_not_count(self):
        """`engagement.accepted_for` reads the newest and only if accepted."""
        from kernel import ledger
        tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")])
        conn = ledger.connect(tmp)
        ledger.insert(conn, "event", task_id="t", claim_id="c1",
                      kind="engagement", actor="worker",
                      payload={"verdict": "refused", "reason": "too short"},
                      created_at="2026")
        conn.close()
        out = self.h._write(tmp)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_the_gate_is_spent_after_the_first_allowed_write(self):
        """A claim raised by a later derive is about code that now exists, so it
        cannot have been answered first -- and asking again would interrupt the
        loop that is fixing what that claim found."""
        from kernel import ledger
        tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")],
                           engaged=["c1"])
        self.assertEqual(self.h._write(tmp), {})           # gate spent here
        conn = ledger.connect(tmp)
        ledger.insert(conn, "claim", id="c2", task_id="t", kind="secret",
                      question="q", subject_refs=json.dumps([]), checker="secret",
                      origin="derive", file="app/m.py", created_at="2026")
        conn.close()
        self.assertEqual(self.h._write(tmp), {})           # not asked again

    def test_a_write_before_derive_does_not_spend_the_gate(self):
        """The gate used to close on any allowed write, including one made while
        the task had no claims at all.

        `derive` is a separate command, so that ordering is a convention and not
        a rule. Measured on one adopter: 2 of 19 tasks wrote before deriving --
        both doc-editing tasks, where a worker has no detector to wait for -- and
        in one of them six engagement-carrying claims appeared 74 seconds after
        the write that had already closed the gate. Neither task was ever asked
        for a sentence.
        """
        from kernel import ledger
        tmp = self.h._repo(kinds=self.KINDS)               # derive has not run
        self.assertEqual(self.h._write(tmp), {})           # nothing to check yet
        self.assertEqual([m.get("basis") for m in self.h._marks(tmp)],
                         ["no-claims"])
        conn = ledger.connect(tmp)                         # now derive runs
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="secret",
                      question="q", subject_refs=json.dumps([]), checker="secret",
                      origin="derive", file="app/m.py", created_at="2026")
        conn.close()
        out = self.h._write(tmp)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("c1", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_malformed_record_does_not_kill_the_hook(self):
        """A hook that dies prints nothing, and nothing is what approval looks
        like.

        `unengaged` bounded its reads with `except sqlite3.Error` while calling
        `json.loads` on a payload inside them, and `issubclass(
        json.JSONDecodeError, sqlite3.Error)` is False. `current_scope` had no
        handler at all. So one malformed row anywhere in a task's engagement or
        widen history took the process down, the agent read the empty output as
        allow, and no `hook_seen` was written either -- the guard was gone and
        the record could not say so.
        """
        from kernel import ledger
        # Written through the kernel, because the ledger refuses direct inserts
        # -- so these are the malformed shapes a real writer can actually leave:
        # valid JSON, wrong structure. `json.loads(...)["added"]` raises
        # KeyError and `"a string".get` raises AttributeError, neither of them a
        # `sqlite3.Error`, which is what the boundary used to name.
        for kind, payload in (("engagement", "a sentence, not an object"),
                              ("scope_widen", {})):
            tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")])
            conn = ledger.connect(tmp)
            ledger.insert(conn, "event", task_id="t",
                          claim_id="c1" if kind == "engagement" else None,
                          kind=kind, actor="worker", payload=payload,
                          created_at="2026")
            conn.close()
            out = self.h._write(tmp)                       # must not crash
            self.assertEqual([m.get("basis") for m in self.h._marks(tmp)],
                             ["unreadable"], kind)

    def test_one_unreadable_answer_does_not_retire_the_gate(self):
        """`claim_kinds.json` unreadable means the hook cannot tell, so it
        allows -- and that allowed write used to close the gate for good.

        Repair the file and the gate stayed shut: one unreadable moment
        permanently retired the check, and the mark said `allowed: true` with
        nothing to distinguish it from a write that had genuinely cleared.
        Allowing is right. Counting it as proof is not.
        """
        tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")])
        kinds = tmp / ".v4" / "claim_kinds.json"
        good = kinds.read_text()
        kinds.write_text("{ not json")
        self.assertEqual(self.h._write(tmp), {})           # cannot tell -> allow
        self.assertEqual(self.h._marks(tmp)[0]["basis"], "unreadable")
        kinds.write_text(good)                             # repaired
        out = self.h._write(tmp)
        self.assertEqual(
            out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_an_upgrade_does_not_reopen_a_gate_a_task_already_passed(self):
        """A mark written by an older hook has no `allowed` and no `basis`.

        Reading a missing field as false made the gate fire at the one moment it
        exists to remove: a task twenty files in, upgraded mid-flight, asked for
        a sentence about code already written. An absent field is not `false`,
        it is "cannot tell", and the row it sits in records a write that already
        went through under the rule of its day.
        """
        from kernel import ledger
        tmp = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")])
        conn = ledger.connect(tmp)
        ledger.insert(conn, "event", task_id="t", claim_id=None,
                      kind="hook_seen", actor="hook",
                      payload={"path": "app/m.py"},        # the old shape
                      created_at="2026")
        conn.close()
        self.assertEqual(self.h._write(tmp), {})

    def test_the_mark_says_why_a_write_was_allowed(self):
        """"Nothing was owed" and "nothing was visible to owe" both used to be
        `allowed: true`, which is what let the first one be mistaken for the
        second. Neither the gate nor an auditor could tell them apart, and the
        distinction cannot be recovered later: a `derive` between the write and
        the reading changes what the same query would say."""
        cleared = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")],
                               engaged=["c1"])
        self.assertEqual(self.h._write(cleared), {})
        self.assertEqual(self.h._marks(cleared)[0]["basis"], "cleared")

        owed = self.h._repo(kinds=self.KINDS, claims=[("c1", "secret")])
        self.h._write(owed)
        self.assertEqual(self.h._marks(owed)[0]["basis"], "owed")


class ASuiteThatDiedDoesNotLookLikeOneThatRan(unittest.TestCase):
    """Twelve lines of progress dots read exactly like twelve lines of a run.

    The tail was fixed at twelve, so a command that died partway printed dots
    and nothing else, and `v4 check` showed `test exit 1` -- the same output as
    a suite that finished with failures. Two states, one answer.
    """

    CHECKER = REPO / "checkers" / "test.py"

    def _run(self, command):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(
            json.dumps({"test_command": command}))
        subject = tmp / "subject.json"
        subject.write_text(json.dumps({"repo_root": str(tmp), "params": {}}))
        return subprocess.run(
            [sys.executable, str(self.CHECKER), "--subject", str(subject)],
            capture_output=True, text=True, timeout=120)

    def test_a_run_that_never_reached_a_summary_says_so(self):
        r = self._run("python3 -c \"import sys;[print('.'*40) for _ in range(60)];"
                      "sys.stderr.write('Segmentation fault');sys.exit(3)\"")
        self.assertEqual(r.returncode, 1)
        self.assertIn("without a run summary", r.stdout)
        self.assertIn("did not finish", r.stdout)

    def test_a_run_that_finished_with_failures_stays_short(self):
        """The long tail is for the case that has no summary, not for every red."""
        r = self._run("python3 -c \"import sys;[print('.'*40) for _ in range(60)];"
                      "print('3 failed, 5 passed in 1.2s');sys.exit(1)\"")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("without a run summary", r.stdout)
        self.assertLessEqual(len(r.stdout.strip().splitlines()), 12)


class TwoWorktreesShareOneLedgerToday(unittest.TestCase):
    """PL-4.  The parts were already there; nothing had run them together.

    The note said parallel needs no code -- `git worktree add`, a shell each,
    the ledger already shared -- and no test held that claim. It holds because
    `ledger_path` resolves through `git rev-parse --git-common-dir`, which is
    the main repo's `.git` from inside any worktree. Written down as an
    executable fact so it stops being a sentence somebody has to trust.
    """

    def test_a_task_opened_in_each_lands_in_one_ledger(self):
        from kernel import config, ledger, lifecycle
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / "app").mkdir()
        (tmp / "app" / "m.py").write_text("X = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, capture_output=True)

        trees = []
        for name in ("a", "b"):
            wt = tmp.parent / f"{tmp.name}-{name}"
            subprocess.run(["git", "worktree", "add", "-q", str(wt), "-b", name],
                           cwd=tmp, capture_output=True)
            self.addCleanup(subprocess.run,
                            ["git", "worktree", "remove", "--force", str(wt)],
                            cwd=tmp, capture_output=True)
            trees.append(wt)

        for wt, tid in zip(trees, ("t-a", "t-b")):
            conn = ledger.connect(wt)
            lifecycle.open_task(conn, config.RepoConfig(tmp), task_id=tid,
                                request="cut", scope_globs=["app/**"])
            conn.close()

        conn = ledger.connect(tmp)
        self.assertEqual(sorted(r[0] for r in conn.execute("SELECT id FROM task")),
                         ["t-a", "t-b"])
        # And the guard notices, which is the half that was missing until today:
        # two open tasks and no `V4_TASK` is a question, not a default.
        self.assertEqual(sorted(ledger.open_task_ids(conn)), ["t-a", "t-b"])


class AMergeExpiresAnswersNobodyIsLeftToRedo(unittest.TestCase):
    """PL-5.  `remerge` has been a declared event kind with no writer.

    B merges, HEAD moves, and every repo-scoped answer A already gave is about
    a tree that is gone -- while A's worker has exited, because exiting is how a
    task finishes. Two questions and a counter; no scheduler, no lock, no
    dispatcher, all of which the notes mark as scale nobody has.
    """

    def _repo(self):
        from kernel import ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t-a", request="r", scope_globs=["**"],
                      base_commit="old", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t-a", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026")
        return conn, tmp

    def _answer(self, conn, cid, head):
        from kernel import ledger
        ledger.append_attempt(conn, claim_id=cid, exit_code=0, stdout="", stderr="",
                              argv="[]", duration_ms=1, subject_digest="{}",
                              checker_sha="x", config_sha="y", worktree="/",
                              head_commit=head, started_at="n", ended_at="n")

    #: `attempt.head_commit` holds a worktree digest for a repo-scoped kind and
    #: a git HEAD for a subject-scoped one. Every other reader filters to
    #: repo-scoped and compares digests; the first version of `stale_after` did
    #: neither, so it compared a digest against a commit hash and reported every
    #: repo-scoped claim in the repo as expired, forever.
    KINDS = {"lint": {"staleness": "repo"}, "test-shape": {"staleness": "subject"}}

    def test_an_answer_at_the_old_digest_is_reported(self):
        from kernel import remerge
        conn, _ = self._repo()
        self._answer(conn, "c1", "digest-A")
        self.assertEqual(remerge.stale_after(conn, "digest-B", kinds_cfg=self.KINDS),
                         [{"task": "t-a", "claims": 1, "at": ["digest-A"]}])
        self.assertEqual(remerge.stale_after(conn, "digest-A", kinds_cfg=self.KINDS), [])

    def test_a_subject_scoped_answer_is_not_expired_by_a_merge(self):
        """It is keyed to the bytes of its own files, and this column holds a
        git HEAD for it -- comparing that to a digest expires it every time."""
        from kernel import ledger, remerge
        conn, _ = self._repo()
        ledger.insert(conn, "claim", id="c2", task_id="t-a", kind="test-shape",
                      question="q", subject_refs="[]", checker="test-shape",
                      origin="derive", created_at="2026")
        self._answer(conn, "c2", "0123456789abcdef")     # a commit, not a digest
        self.assertEqual(remerge.stale_after(conn, "digest-B", kinds_cfg=self.KINDS), [])

    def test_rounds_are_counted_and_bounded(self):
        """Three rounds is a fact about where the cuts were, not a worker to
        retry. `ship` bounds its own loop the same way and says the same."""
        from kernel import remerge
        conn, _ = self._repo()
        self._answer(conn, "c1", "digest-A")
        for expected in (1, 2):
            n, blocked = remerge.record(conn, task_id="t-a", head="new", claims=1)
            self.assertEqual((n, blocked), (expected, False))
        n, blocked = remerge.record(conn, task_id="t-a", head="new", claims=1)
        self.assertEqual((n, blocked), (3, True))
        reasons = [json.loads(r[0]).get("reason") for r in conn.execute(
            "SELECT payload FROM event WHERE kind = 'blocked'")]
        self.assertEqual(reasons, ["remerge_not_converging"])

    def test_a_shipped_task_is_not_asked_to_redo_anything(self):
        from kernel import ledger, remerge
        conn, _ = self._repo()
        self._answer(conn, "c1", "digest-A")
        ledger.insert(conn, "event", task_id="t-a", claim_id=None, kind="shipped",
                      actor="kernel", payload={"claims": 1}, created_at="2026")
        self.assertEqual(remerge.stale_after(conn, "digest-B",
                                             kinds_cfg=self.KINDS), [])


class SevenOfEightUnexecutedIsNotACleanPass(unittest.TestCase):
    """PL-10.  The threshold was "none of them", so most of them read as fine.

    A green suite that reached one changed file out of eight exits 0 and says
    nothing, while the trace that knows which seven it missed has already run.
    Refusing would be wrong -- a config-only change is one no test can reach --
    but silence is not the other option.
    """

    CHECKER = REPO / "checkers" / "test.py"

    def _repo(self, files, command):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps({"test_command": command}))
        (tmp / "base.py").write_text("X = 0\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, capture_output=True)
        for rel, body in files.items():
            (tmp / rel).write_text(body)
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "work"], cwd=tmp, capture_output=True)
        subject = tmp / "subject.json"
        subject.write_text(json.dumps(
            {"repo_root": str(tmp), "params": {}, "diff_base": "HEAD~1"}))
        return subprocess.run(
            [sys.executable, str(self.CHECKER), "--subject", str(subject)],
            capture_output=True, text=True, timeout=180)

    def test_the_ones_the_suite_never_reached_are_named(self):
        r = self._repo(
            {"touched.py": "def f():\n    return 1\n",
             "never.py": "def g():\n    return 2\n",
             "also_never.py": "def h():\n    return 3\n"},
            f"{sys.executable} -c \"import touched; touched.f(); print('1 passed')\"")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("never executed", r.stdout)
        self.assertIn("never.py", r.stdout)
        self.assertIn("also_never.py", r.stdout)
        self.assertNotIn("  touched.py", r.stdout)   # it ran; not in the list

    def test_reaching_none_of_them_is_still_a_failure(self):
        """The gate above this does not move."""
        r = self._repo(
            {"never.py": "def g():\n    return 2\n"},
            f"{sys.executable} -c \"print('1 passed')\"")
        self.assertEqual(r.returncode, 1)
        self.assertIn("executed none of the", r.stderr)


class CommittingIsNotAChange(unittest.TestCase):
    """The digest said "content" and hashed content *relative to a commit*.

    Measured on a five-task run: `v4 check` green, `git commit` with not one
    byte altered, `v4 ship`, and every repo-scoped claim came back STALE.
    HEAD moved and the diff emptied, so the key moved. The ordinary flow --
    check, commit, ship -- could not converge.
    """

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / "app").mkdir()
        (tmp / "app" / "m.py").write_text("X = 1\n")
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"derive_exclude": ["**/__pycache__/**"]}))
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, capture_output=True)
        return tmp

    def _git(self, tmp, *a):
        subprocess.run(["git", *a], cwd=tmp, capture_output=True)

    def test_staging_does_not_move_it(self):
        tmp = self._repo()
        (tmp / "app" / "n.py").write_text("Y = 1\n")
        before = hashing.worktree_digest(tmp)
        self._git(tmp, "add", "-A")
        self.assertEqual(hashing.worktree_digest(tmp), before)

    def test_committing_does_not_move_it(self):
        tmp = self._repo()
        (tmp / "app" / "n.py").write_text("Y = 1\n")
        self._git(tmp, "add", "-A")
        before = hashing.worktree_digest(tmp)
        self._git(tmp, "commit", "-qm", "n")
        self.assertEqual(hashing.worktree_digest(tmp), before)

    def test_a_byte_moves_it(self):
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        (tmp / "app" / "m.py").write_text("X = 2\n")
        self.assertNotEqual(hashing.worktree_digest(tmp), before)

    def test_an_untracked_file_moves_it(self):
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        (tmp / "app" / "n.py").write_text("Y = 1\n")
        self.assertNotEqual(hashing.worktree_digest(tmp), before)

    def test_deleting_moves_it_and_restoring_returns_it(self):
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        (tmp / "app" / "m.py").unlink()
        self.assertNotEqual(hashing.worktree_digest(tmp), before)
        (tmp / "app" / "m.py").write_text("X = 1\n")
        self.assertEqual(hashing.worktree_digest(tmp), before)

    def test_an_excluded_artefact_still_does_not_move_it(self):
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        cache = tmp / "app" / "__pycache__"
        cache.mkdir()
        (cache / "m.cpython-311.pyc").write_bytes(b"\x00compiled")
        self.assertEqual(hashing.worktree_digest(tmp), before)

    def test_kernel_output_still_does_not_move_it(self):
        tmp = self._repo()
        before = hashing.worktree_digest(tmp)
        (tmp / ".v4" / "risks").mkdir()
        (tmp / ".v4" / "risks" / "c1.json").write_text("{}")
        (tmp / ".v4" / "ledger_export.jsonl").write_text("{}\n")
        self.assertEqual(hashing.worktree_digest(tmp), before)


class TheAfterGateHasToHaveACaller(unittest.TestCase):
    """`doctor` asked this about hooks and never about the gate above them.

    `v4 sweep` answers "is it due" and starts nothing -- SPEC.md §10.1 calls
    `--if-due` the form "for cron to give up on" -- so the trigger lives outside
    the kernel, exactly like a hook's, and exactly like a hook it can simply not
    exist. Measured: no workflow, no command and no config key mentioned
    `sweep`, while 145 of the 254 migrated rules (57%) had landed in the lens
    layer this gate is the only trigger for.
    """

    def _repo(self, *, lenses=1, caller=None, config_extra=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / ".v4" / "lenses").mkdir(parents=True)
        for i in range(lenses):
            (tmp / ".v4" / "lenses" / f"l{i}.json").write_text(
                json.dumps({"name": f"l{i}", "source": "s", "checks": ["Inspect the actual configured behavior"],
                            "anti_patterns": []}))
        cfg = {"test_command": "true", "policy": "allow_accepted_risk"}
        cfg.update(config_extra or {})
        (tmp / ".v4" / "config.json").write_text(json.dumps(cfg))
        if caller:
            where, body = caller
            (tmp / where).parent.mkdir(parents=True, exist_ok=True)
            (tmp / where).write_text(body)
        return tmp

    def _row(self, tmp):
        from kernel import doctor
        for r in doctor.run(tmp):
            if r.get("what") == "maintenance schedule":
                return f"{r.get('detail','')} {r.get('fix','')}"
        return None

    def test_lenses_and_nobody_calling_is_reported(self):
        r = self._row(self._repo())
        self.assertIsNotNone(r)
        self.assertIn("no native job observation", r)

    def test_a_workflow_mention_is_not_a_native_job_observation(self):
        r = self._row(self._repo(
            caller=(".github/workflows/x.yml", "on:\n  schedule:\n"
                    "jobs:\n  s:\n    steps:\n      - run: v4 sweep --if-due\n")))
        self.assertIn("no native job observation", r)

    def test_a_command_mention_is_not_a_native_job_observation(self):
        r = self._row(self._repo(
            caller=(".claude/commands/run.md", "run `v4 sweep` when due\n")))
        self.assertIn("no native job observation", r)

    def test_a_repo_with_no_lenses_is_not_asked(self):
        self.assertIsNone(self._row(self._repo(lenses=0)))

    def test_default_cadence_does_not_imply_a_job(self):
        r = self._row(self._repo(caller=(".claude/commands/run.md", "v4 sweep\n")))
        self.assertIn("no native job observation", r)

    def test_declared_cadence_does_not_imply_a_job(self):
        r = self._row(self._repo(
            caller=(".claude/commands/run.md", "v4 sweep\n"),
            config_extra={"lens_sweep": {"every_days": 7}}))
        self.assertIn("no native job observation", r)


class CoverageNeedsADenominatorThatRecurs(unittest.TestCase):
    """557 ids, two thirds of them generated per phase, is not a denominator.

    `kernel/coverage.py`'s own docstring said so: `PO-DL-nnn` is created for one
    phase and never recurs, and a coverage figure against a set that is mostly
    one-off by construction says nothing. The 17 operational-risk classes are
    the replacement -- risk classes rather than stack mechanics, from sources
    that move on a multi-year cadence.
    """

    ROWS = [
        {"n": 1, "category": "Truth ownership", "trigger": "t", "fill": "judgment",
         "answered_by": [], "coverage": "none"},
        {"n": 2, "category": "Secrets", "trigger": "t", "fill": "mixed",
         "answered_by": ["secret"], "coverage": "answered"},
        {"n": 3, "category": "Adapter boundary", "trigger": "t", "fill": "judgment",
         "answered_by": ["layer-boundary"], "coverage": "partial"},
    ]

    def _repo(self, rows=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        if rows is not None:
            (tmp / ".v4" / "risk_rubric.json").write_text(json.dumps(
                {"source": "s", "why": "w", "rows": rows}))
        return tmp

    def test_it_says_how_many_have_a_mechanism(self):
        from kernel import coverage
        out = "\n".join(coverage.risk_report(self._repo(self.ROWS),
                                             {"secret", "layer-boundary"}))
        self.assertIn("2 have a mechanism here, 1 have none", out)

    def test_a_kind_this_repo_did_not_install_does_not_count(self):
        from kernel import coverage
        out = "\n".join(coverage.risk_report(self._repo(self.ROWS), {"secret"}))
        self.assertIn("1 have a mechanism here, 2 have none", out)

    def test_the_judgment_rows_are_counted_separately(self):
        # These are the ones no checker reaches, so the headline that hides
        # them inside an average is the one worth refusing.
        from kernel import coverage
        out = "\n".join(coverage.risk_report(self._repo(self.ROWS),
                                             {"secret", "layer-boundary"}))
        self.assertIn("1 of the 2 marked `judgment`", out)

    def test_no_rubric_is_not_an_empty_report(self):
        from kernel import coverage
        self.assertIsNone(coverage.risk_report(self._repo(None), set()))

    def test_this_repo_ships_one_and_it_parses(self):
        from kernel import coverage
        doc = coverage.rubric(REPO)
        self.assertIsNotNone(doc)
        self.assertEqual(len(doc["rows"]), 17)
        for r in doc["rows"]:
            self.assertIn(r["fill"], ("judgment", "mixed"))
            self.assertIn(r["coverage"], ("none", "partial", "answered"))
            # A row claiming coverage has to name what covers it.
            if r["coverage"] != "none":
                self.assertTrue(r["answered_by"], r["category"])

    def test_every_kind_the_mapping_names_is_registered(self):
        """`MAPPING` is a judgement about the registry, and nothing compared it
        to one.

        `PO-6` read `[]  # surface truth -- no surface checker yet` while
        `surface-proof` and `runtime-proof` were both registered with their own
        detectors -- so every `v4 coverage --predecessor` run reported that
        obligation family unanswered, on the strength of a comment.
        """
        from kernel import coverage
        kinds = set(json.loads((REPO / ".v4/claim_kinds.json").read_text()))
        named = {k for names in coverage.MAPPING.values() for k in names}
        self.assertTrue(named)
        self.assertEqual(sorted(named - kinds), [])

    def test_no_family_is_left_with_nothing_answering_it(self):
        """The direction that went stale, and the only one a program can put.

        `PO-6` was `[]` with a comment saying no surface checker existed, and
        two did. Whether an *empty* row is honest is a judgement -- the comment
        said "yet" and nothing can settle a "yet" -- so what is asserted is the
        state this repo is actually in: every family it catalogues has at least
        one kind. A new family arrives empty and fails here until somebody
        either names what answers it or says in the catalogue that nothing
        does.
        """
        from kernel import coverage
        empty = sorted(po for po, names in coverage.MAPPING.items() if not names)
        self.assertEqual(empty, [])


class ADeliveredPieceSaysWhatDoneMeans(unittest.TestCase):
    """`--quote` and `--symbol` answer the accounting half only.

    The check is `str.__contains__` plus a character ratio, so a task that
    quotes the request perfectly, names a real symbol, and implements something
    else scores 100%. Measured on the X/Y run: three of eight briefs came back
    with less than they asked for, and every one of them would have passed.

    The doctrine already required this and had no mechanism: 「驗收門檻由請求者
    給出。只有程式碼已經決定了的,才可以推斷。」 Deliberately not judged -- a
    checker that reads a request and decides whether it was met is V3's
    prose-derivation machine again.
    """

    REQ = "加個 get，順手擋住空 key"

    def _fault(self, **entry):
        from kernel import request_cover
        entry.setdefault("quote", "加個 get")
        return request_cover.fault(self.REQ, entry)

    def test_delivered_with_no_acceptance_is_refused(self):
        self.assertIn("what would make it done", self._fault(symbol="get"))

    def test_delivered_with_one_is_accepted(self):
        self.assertIsNone(self._fault(
            symbol="get", acceptance="store.get('nope') returns None"))

    def test_a_few_characters_do_not_count(self):
        self.assertIsNotNone(self._fault(symbol="get", acceptance="ok"))

    def test_not_done_owes_a_reason_and_not_an_acceptance(self):
        # Deciding not to do a piece already owes `--why`; asking for an
        # acceptance condition on something nobody built is asking for fiction.
        from kernel import request_cover
        self.assertIsNone(request_cover.fault(self.REQ, {
            "quote": "加個 get", "not_done": True,
            "why": "呢半要一個 migration，唔喺呢個 task 嘅 scope 入面，開咗 t-9 跟"}))

    def test_it_is_stored_and_read_back(self):
        from kernel import ledger, request_cover
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t", request=self.REQ, scope_globs="[]",
                      base_commit="0", created_at="n")
        request_cover.record(conn, task_id="t", request=self.REQ,
                             quote="加個 get", symbol="get",
                             acceptance="store.get('nope') returns None")
        self.assertEqual(request_cover.entries(conn, "t")[0]["acceptance"],
                         "store.get('nope') returns None")


class ARuleThatAsksForSomethingTheRepoCannotDo(unittest.TestCase):
    """`review-finding` required a durable target and there was none.

    Its fifth rule says 「而家唔修嘅嘢,要有一個 durable 嘅目標指住。」 and
    `accepted_risk` is not it: per-claim, lapsing against its own key, right for
    "cannot be proved today" and wrong for "real, and somebody will fix it".

    So the rule stood for as long as it existed asking for something the repo
    could not do -- and this repo's doctrine names that: 「一條長期被違反而從未
    被執行的規則,要麼執行它,要麼改掉它。兩樣都不做不是一個選項。」
    """

    def _conn(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        return ledger.connect(tmp), tmp

    WHY = ("this finding is real, but repairing it narrows a detector and that "
           "expires a dozen answered claims; not inside this task")

    def test_a_deferral_with_no_target_is_refused(self):
        conn, tmp = self._conn()
        with self.assertRaises(review.CannotDefer) as e:
            review.defer(conn, tmp, claim_id="c1", why=self.WHY, target="")
        self.assertIn("names where the work went", str(e.exception))

    def test_a_deferral_with_no_reason_is_refused(self):
        conn, tmp = self._conn()
        with self.assertRaises(review.CannotDefer):
            review.defer(conn, tmp, claim_id="c1", why="later", target="t-42")

    def test_a_complete_deferral_lands_in_git_and_in_the_ledger(self):
        conn, tmp = self._conn()
        p = review.defer(conn, tmp, claim_id="c1", why=self.WHY, target="t-42")
        self.assertTrue(p.is_file())
        self.assertEqual(json.loads(p.read_text())["target"], "t-42")
        self.assertEqual(review.deferred(conn), [("c1", "t-42")])

    def test_the_record_survives_a_clone(self):
        # The ledger lives in .git/ and a clone has none, so a deferral that
        # only existed as an event would vanish the moment somebody else looked.
        conn, tmp = self._conn()
        review.defer(conn, tmp, claim_id="c1", why=self.WHY, target="t-42")
        self.assertTrue((tmp / review.DEFER_DIR / "c1.json").is_file())

    def test_two_deferrals_both_show(self):
        conn, tmp = self._conn()
        review.defer(conn, tmp, claim_id="c1", why=self.WHY, target="t-42")
        review.defer(conn, tmp, claim_id="c2", why=self.WHY, target="ISSUE-7")
        self.assertEqual(dict(review.deferred(conn)),
                         {"c1": "t-42", "c2": "ISSUE-7"})


class SomeWorkIsLargerThanOneContext(unittest.TestCase):
    """One request per task, and no way to say "the rest goes here".

    The argument for one-request-one-task holds: a different proof surface is
    not a reason to split. But it disproves one wrong way to split, not the
    claim that some work exceeds a context -- and the real usage is a long
    chain, F167 to F238 in the reference repo.

    Without a carry-forward a worker at its limit has three moves: push on until
    it breaks, open a fresh task that knows nothing and may undo the last one,
    or paste a summary it invented, with no shape and no check.
    """

    def test_the_chain_is_a_query_and_not_a_line_of_text(self):
        """`after` was read once to build a block of prose and then dropped.

        The edge existed only as `[continues <id>]` inside `task.request`, so
        "what continued this task" had no answer a query could give -- and
        `carry_forward`'s own docstring says it writes "facts the ledger already
        holds", which was true of everything in the block except the link
        itself. Measured on one adopter: eighteen tasks, one of them opened only
        because the previous one hit something, and not a single `--after`.
        """
        conn, cfg = self._repo()
        lifecycle.open_task(conn, cfg, task_id="t1", request="first cut",
                            scope_globs=["**"])
        lifecycle.open_task(conn, cfg, task_id="t2", request="second cut",
                            scope_globs=["**"], after="t1")
        lifecycle.open_task(conn, cfg, task_id="t3", request="third cut",
                            scope_globs=["**"], after="t1")
        self.assertEqual(lifecycle.continues(conn, "t2"), "t1")
        self.assertIsNone(lifecycle.continues(conn, "t1"))
        self.assertEqual(lifecycle.continued_by(conn, "t1"), ["t2", "t3"])
        self.assertEqual(lifecycle.continued_by(conn, "t2"), [])

    def test_the_command_that_drives_every_task_names_after(self):
        """A mechanism built for "the real usage is a long chain, F167 to F238"
        was absent from the one file an orchestrator follows, so it was never
        reached for -- eighteen tasks, zero uses."""
        run_md = (REPO / ".claude" / "commands" / "run.md").read_text(encoding="utf-8")
        self.assertIn("--after", run_md)

    def test_the_marker_the_writer_writes_is_the_one_the_reader_matches(self):
        """One rule, one definition.

        `carry_forward` wrote `[continues <id>]` as a literal and
        `request_cover.own` matched it with a regex written out separately.
        Nothing held the two together: change either spelling and the reader
        stops recognising the writer, so every clause carried from the previous
        task counts as this task's own work again -- silently, with the coverage
        number quietly going wrong and no error anywhere. Deriving the pattern
        from the same constant the writer uses makes a one-sided change
        impossible rather than merely discouraged.
        """
        from kernel import request_cover
        from kernel.lifecycle import carry_forward
        import inspect
        for after in ("t-prev", "t-lm-render", "F238", "a/b-c_1"):
            line = request_cover.carried_line(after)
            self.assertTrue(request_cover._CARRIED.search(line), line)
            self.assertEqual(request_cover.own(f"mine\n\n{line}\n  x: y"),
                             "mine")
        # And the writer reaches it through that function rather than spelling
        # the marker again -- a second literal would pass every case above.
        self.assertNotIn("[continues", inspect.getsource(carry_forward))

    def _repo(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["init", "-q"], ["config", "user.email", "t@t"],
                  ["config", "user.name", "t"]):
            subprocess.run(["git", *a], cwd=tmp, capture_output=True)
        (tmp / "a.py").write_text("X = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "b"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        conn = ledger.connect(tmp)
        return conn, config.RepoConfig(tmp)

    def _first(self, conn, cfg):
        lifecycle.open_task(conn, cfg, task_id="t-1", request="rewrite payments",
                            scope_globs=["app/**", "tests/**"])
        ledger.insert(conn, "claim", id="c1", task_id="t-1", kind="secret",
                      question="q", file="", symbol="", variant="",
                      subject_refs="[]", checker="s", detector="",
                      detector_sha="", origin="detector", created_at="n")
        ledger.insert(conn, "claim", id="c2", task_id="t-1", kind="test",
                      question="q", file="", symbol="", variant="",
                      subject_refs="[]", checker="t", detector="",
                      detector_sha="", origin="detector", created_at="n")
        ledger.append_attempt(conn, claim_id="c1", exit_code=0, stdout="",
                              stderr="", argv="[]", duration_ms=1,
                              subject_digest="{}", checker_sha="x",
                              config_sha="y", worktree="/", head_commit="h",
                              started_at="n", ended_at="n")
        return conn

    def test_the_next_task_inherits_what_the_last_one_settled(self):
        conn, cfg = self._repo()
        self._first(conn, cfg)
        lifecycle.open_task(conn, cfg, task_id="t-2", request="the other half",
                            scope_globs=["app/**"], after="t-1")
        req = conn.execute("SELECT request FROM task WHERE id='t-2'").fetchone()[0]
        self.assertIn("the other half", req)
        self.assertIn("rewrite payments", req)
        self.assertIn("answered   : secret", req)
        self.assertIn("still open : test", req)

    def test_without_after_nothing_is_carried(self):
        conn, cfg = self._repo()
        self._first(conn, cfg)
        lifecycle.open_task(conn, cfg, task_id="t-2", request="unrelated",
                            scope_globs=["app/**"])
        req = conn.execute("SELECT request FROM task WHERE id='t-2'").fetchone()[0]
        self.assertEqual(req, "unrelated")

    def test_continuing_a_task_that_is_not_there_is_refused(self):
        conn, cfg = self._repo()
        with self.assertRaises(RuntimeError):
            lifecycle.open_task(conn, cfg, task_id="t-2", request="x",
                                scope_globs=["**"], after="t-nope")

    def test_it_carries_facts_and_not_a_narrative(self):
        # A narrative is the prose contract this project removed. Everything in
        # the block has to be something the ledger already holds.
        conn, cfg = self._repo()
        self._first(conn, cfg)
        block = lifecycle.carry_forward(conn, "t-1")
        for line in block.splitlines()[1:]:
            self.assertRegex(line.strip(), r"^(its request|its scope|answered|still open)")


class WhetherADetectorReadsTheTableIsAnASTQuestion(unittest.TestCase):
    """The verdict decides whether a broken table can switch a detector off.

    Two earlier criteria were wrong in opposite directions. Searching the raw
    source for `facts.get(` counted a docstring and missed `f = facts` -- the
    method four of this repo's own bypass fixtures were written against.
    Seeding an AST walk with likely aliases (`table`, `f`) made every `f.read()`
    a hit, and three detectors that do nothing but declare `--facts` came back
    conditional. A guess about what a variable is probably called is the same
    mistake as searching the text, wider.

    Every detector declares `--facts`; the contract requires it. Consuming the
    parsed value is the decidable difference.
    """

    def t(self, src):
        return derive._touches_facts(src)

    def test_reading_the_parsed_value_counts(self):
        self.assertTrue(self.t('p.add_argument("--facts")\n'
                               'a = p.parse_args()\n'
                               't = load(a.facts)\n'))

    def test_declaring_it_and_never_touching_it_does_not(self):
        self.assertFalse(self.t('p.add_argument("--facts")\n'
                                'a = p.parse_args()\n'
                                'print(a.subject)\n'))

    def test_a_docstring_mentioning_it_does_not(self):
        # The text version counted this.
        self.assertFalse(self.t('def d():\n    """reads a.facts"""\n    return 1\n'))

    def test_an_unrelated_f_does_not(self):
        # The alias-seeded version counted this.
        self.assertFalse(self.t('p.add_argument("--facts")\n'
                                'with open(x) as f:\n    f.read()\n'))

    def test_the_flag_named_inside_add_argument_is_not_a_read(self):
        self.assertFalse(self.t('p.add_argument("--facts", dest="facts")\n'))

    def test_unparsable_source_is_not_a_read(self):
        self.assertFalse(self.t("def broken(:\n"))

    def test_this_repo_has_exactly_the_ones_listed(self):
        """Ground truth by inspection: only these reference the parsed value
        outside the declaration. One list, held in the class that states it."""
        from tests.test_kernel import ReadsFactsFollowsImports as R
        got = {p.name for p in (REPO / "detectors").glob("*.py")
               if not p.name.startswith("_") and derive.reads_facts(p)}
        self.assertEqual(got, R.READS_THE_TABLE)


class ChecksThatReadNothingReportClean(unittest.TestCase):
    """Two checks in this repo were reading zero of what they judge.

    `gate_colours_named` exists because `bypass/` was enforced by the gate and
    absent from the contract. Its regex was `^([A-Z_]+)\\s*=\\s*"([a-z]+)"`, and
    the gate declares its colours as one tuple -- `RED, GREEN, BYPASS = "red",
    "green", "bypass"` -- which that pattern cannot match. It had found zero
    colours since the day it was written and reported clean every time.

    `counted_claims` read ten nouns typed into a regex, while the repo can count
    every noun it should know. SPEC §13.5 already records what that costs.
    """

    def _mod(self):
        # Was loaded from `checkers/spec_coverage.py` by path, because that is
        # where the rules were. They are `kernel/spec_coverage.py` now, so this
        # is an ordinary import, which is the difference the move was for.
        from kernel import spec_coverage
        return spec_coverage

    def test_a_tuple_declaration_is_read(self):
        m = self._mod()
        spec_text = (REPO / "docs" / "SPEC.md").read_text()
        self.assertEqual(m.gate_colours_named(REPO, spec_text), [])

    def test_a_colour_the_spec_stops_naming_is_reported(self):
        m = self._mod()
        spec_text = (REPO / "docs" / "SPEC.md").read_text().replace("bypass/", "XX/")
        found = m.gate_colours_named(REPO, spec_text)
        self.assertTrue(any("bypass/" in p for p in found), found)

    def test_reading_no_colour_at_all_is_itself_reported(self):
        # The state its predecessor sat in. Silence has to be a finding.
        m = self._mod()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "kernel").mkdir()
        (tmp / "kernel" / "register.py").write_text("X = 1\n")
        found = m.gate_colours_named(tmp, "nothing here")
        self.assertTrue(any("reports clean when it reads nothing" in p
                            for p in found), found)

    def test_the_noun_list_comes_from_the_repo(self):
        m = self._mod()
        truth = m._reality(REPO)
        r = m._count_re(truth)
        # Four groups, not three: the quantifier is captured rather than
        # consumed, because it became optional. `docs/FACTS.md` is an English
        # document whose subject is a set of tables, so its own row counts had
        # no Chinese-quantified form to be written in, and the count that
        # decides whether every detector reads a clean repo was the one count
        # nothing settled. `_names_its_population` is what keeps that from
        # meaning "any digit next to any noun".
        self.assertEqual(r.findall("27 個 checker"), [("27", "個", "", "checker")])
        # Two shapes the typed-in version could not see. The qualifier is
        # captured rather than skipped -- `counted_claims` needs it to tell a
        # renaming (`reviewer lens` is `lens`) from a narrowing (`conditional
        # detector` is not the count of `detectors/`).
        self.assertEqual(r.findall("5 種 claim kind"),
                         [("5", "種", "claim ", "kind")])
        self.assertEqual(r.findall("9 項 reviewer lens"),
                         [("9", "項", "reviewer ", "lens")])
        # And a noun the repo cannot count stays out of it.
        self.assertEqual(r.findall("3 個 adapter"), [])

    def test_a_count_with_no_quantifier_has_to_name_its_table(self):
        m = self._mod()
        narrowing = {"auth_decision", "conditional"}
        # `8 auth_decision rows`: the whole name is the key, so the qualifier
        # group is empty and the noun carries the table. Reading only the
        # qualifier missed exactly this, which is the case it was written for.
        self.assertTrue(m._names_its_population("", "", "auth_decision row",
                                                narrowing))
        self.assertTrue(m._names_its_population("", "conditional ", "detector",
                                                narrowing))
        # `27 checkers` is not a claim about this repo's registry, and the
        # measurement says so: dropping the requirement turns 0 findings into
        # 50 across `docs/`.
        self.assertFalse(m._names_its_population("", "", "checker", narrowing))
        self.assertFalse(m._names_its_population("", "of ", "checker", narrowing))
        # A Chinese quantifier is the older route and still carries on its own.
        self.assertTrue(m._names_its_population("個", "", "checker", narrowing))

    def test_a_narrowed_count_is_not_answered_with_the_whole_population(self):
        """`15 個 conditional detector` is about the registry, not `detectors/`.

        Dropping the qualifier made this check report a drift that was not
        there -- §8.8's row says 15 and is right -- while the sentence that
        narrows a count to something nothing counts went on saying anything.
        """
        m = self._mod()
        tmp = self._repo_with(m, "registry 有 2 個 conditional detector,"
                                 "`detectors/` 有 3 個 detector")
        self.assertEqual(m.counted_claims(tmp), [])

    def test_and_a_narrowed_count_that_is_wrong_is_still_reported(self):
        """The control: refusing to answer would pass the test above."""
        m = self._mod()
        tmp = self._repo_with(m, "registry 有 9 個 conditional detector")
        said = m.counted_claims(tmp)
        self.assertEqual(len(said), 1, said)
        self.assertIn("9 conditional detector, and there are 2", said[0])

    def _repo_with(self, m, sentence):
        """A repo with 2 registered detectors and 3 files under `detectors/`."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "docs").mkdir()
        (tmp / "docs" / "d.md").write_text(sentence + "\n", encoding="utf-8")
        (tmp / "detectors").mkdir()
        for name in ("a.py", "b.py", "always_c.py"):
            (tmp / "detectors" / name).write_text("x = 1\n")
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "detectors.json").write_text(json.dumps(
            {"a.py": {"path": "detectors/a.py"},
             "b.py": {"path": "detectors/b.py"}}))
        return tmp


class TheDoctrineOwnsASectionNotTheFile(unittest.TestCase):
    """`write` was `p.write_text(render(cfg))` -- the whole file, every time.

    That holds while the only adopter is this framework, whose CLAUDE.md is
    nothing but generated. Measured on a real one: `adopter_a` carries 381
    lines there, about 250 of them its own operating contract -- Role, Core
    Principles, Project Identity, 180 lines of MUST rules -- and a cutover would
    have deleted every one on the first `v4 doctrine --write`.
    """

    OWN = "# My Repo\n\n## MUST Rules\n\n- never commit a credential\n"

    def _repo(self, claude=None):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "doctrine": True}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"secret": {"checker": "s", "question_template": "q",
                        "staleness": "repo"}}))
        if claude is not None:
            (tmp / "CLAUDE.md").write_text(claude)
        return config.RepoConfig(tmp)

    def test_an_existing_file_keeps_every_line_of_itself(self):
        from kernel import doctrine
        cfg = self._repo(self.OWN)
        doctrine.write(cfg)
        text = (cfg.root / "CLAUDE.md").read_text()
        self.assertIn("never commit a credential", text)
        self.assertIn(doctrine.BEGIN, text)

    def test_writing_twice_does_not_stack_blocks(self):
        from kernel import doctrine
        cfg = self._repo(self.OWN)
        doctrine.write(cfg)
        doctrine.write(cfg)
        text = (cfg.root / "CLAUDE.md").read_text()
        self.assertEqual(text.count(doctrine.BEGIN), 1)
        self.assertEqual(text.count(doctrine.END), 1)

    def test_the_repo_half_survives_a_regeneration(self):
        from kernel import doctrine
        cfg = self._repo(self.OWN)
        doctrine.write(cfg)
        before = (cfg.root / "CLAUDE.md").read_text().split(doctrine.BEGIN)[0]
        # Something changes in the registry, so the block is rewritten.
        kinds = json.loads((cfg.root / ".v4" / "claim_kinds.json").read_text())
        kinds["test"] = {"checker": "t", "question_template": "q",
                         "staleness": "repo"}
        (cfg.root / ".v4" / "claim_kinds.json").write_text(json.dumps(kinds))
        doctrine.write(config.RepoConfig(cfg.root))
        after = (cfg.root / "CLAUDE.md").read_text().split(doctrine.BEGIN)[0]
        self.assertEqual(before, after)

    def test_content_after_the_block_survives_too(self):
        from kernel import doctrine
        cfg = self._repo(self.OWN)
        doctrine.write(cfg)
        p = cfg.root / "CLAUDE.md"
        p.write_text(p.read_text() + "\n## Written after\n\n- still here\n")
        doctrine.write(config.RepoConfig(cfg.root))
        self.assertIn("still here", p.read_text())

    def test_a_file_with_no_block_is_drift(self):
        from kernel import doctrine
        cfg = self._repo(self.OWN)
        self.assertIn("carries no generated block", doctrine.drift(cfg) or "")

    def test_a_written_block_is_not_drift(self):
        from kernel import doctrine
        cfg = self._repo(self.OWN)
        doctrine.write(cfg)
        self.assertIsNone(doctrine.drift(config.RepoConfig(cfg.root)))

    def test_editing_the_generated_half_is_drift(self):
        from kernel import doctrine
        cfg = self._repo(self.OWN)
        doctrine.write(cfg)
        p = cfg.root / "CLAUDE.md"
        p.write_text(p.read_text().replace("永遠適用", "永遠適用（我自己改咗）"))
        self.assertIsNotNone(doctrine.drift(config.RepoConfig(cfg.root)))


class TwoOpenTasksIsNotAGuessToMake(unittest.TestCase):
    """The hook picked the newest open task and said nothing about the choice.

    Right until the write was for the other one -- then it checked against a
    scope that was not its own, passed, and left a `hook_seen` naming a task
    that did not make the write. Measured on one adopter: two tasks open three
    times in one day, every time because work finished and nobody ran `ship`.
    Refusing costs a sentence; guessing costs the guard.
    """

    def setUp(self):
        self.h = TheHookSaysWhatItDecided()
        self.h.addCleanup = self.addCleanup

    def _second_task(self, tmp):
        from kernel import ledger
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t2", request="another",
                      scope_globs=["app/**"], base_commit="x", created_at="2026")
        conn.close()

    def _write_without_v4_task(self, tmp, rel="app/m.py"):
        import os
        env = dict(os.environ, V4_REPO=str(tmp))
        env.pop("V4_TASK", None)
        r = subprocess.run(
            [sys.executable, str(self.h.HOOK)],
            input=json.dumps({"tool_name": "Edit",
                              "tool_input": {"file_path": str(tmp / rel)}}),
            capture_output=True, text=True, env=env, timeout=60)
        return json.loads(r.stdout or "{}")

    def test_one_open_task_is_still_inferred(self):
        """The convenience the inference exists for stays."""
        tmp = self.h._repo()
        self.assertEqual(self._write_without_v4_task(tmp), {})

    def test_two_open_tasks_refuse_and_name_both(self):
        tmp = self.h._repo()
        self._second_task(tmp)
        out = self._write_without_v4_task(tmp)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("t2", reason)
        self.assertIn("t", reason)
        self.assertIn("V4_TASK", reason)
        self.assertIn("ship", reason)

    def test_the_ambiguous_refusal_is_on_the_record(self):
        """The one path where this hook actually blocks wrote no `hook_seen`.

        So `ship` reported "the write hook never fired for this task" -- the
        same output as a hook that was never installed -- for a task whose write
        the hook had just refused. Which task the write was for is the unknown,
        so the mark goes on every candidate rather than on a guess.
        """
        from kernel import ledger
        tmp = self.h._repo()
        self._second_task(tmp)
        out = self._write_without_v4_task(tmp)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        conn = ledger.connect(tmp)
        rows = conn.execute(
            "SELECT task_id, payload FROM event WHERE kind='hook_seen'").fetchall()
        conn.close()
        self.assertTrue(rows, "the hook refused a write and recorded nothing")
        self.assertEqual({r["task_id"] for r in rows}, {"t", "t2"},
                         "the refusal was recorded against one task, which is "
                         "the guess this path exists to avoid")
        for r in rows:
            self.assertFalse(json.loads(r["payload"])["allowed"])

    def test_saying_which_one_makes_it_work_again(self):
        tmp = self.h._repo()
        self._second_task(tmp)
        self.assertEqual(self.h._write(tmp), {})          # `_write` sets V4_TASK=t

    def test_shipping_the_finished_one_makes_it_work_again(self):
        from kernel import ledger
        tmp = self.h._repo()
        self._second_task(tmp)
        conn = ledger.connect(tmp)
        ledger.insert(conn, "event", task_id="t2", claim_id=None, kind="shipped",
                      actor="kernel", payload={"claims": 0}, created_at="2026")
        conn.close()
        self.assertEqual(self._write_without_v4_task(tmp), {})


class AskedBeforeTheCutRatherThanAfterIt(unittest.TestCase):
    """PL-6.  Every measured `scope widen` had one cause.

    Something inside the scope is written out by hand in a file outside it: a
    flow id in a list, a handler name in a registry table, a digest pinned
    byte-for-byte. The worker finds out when the checker says the diff left
    bounds, four hours after the moment it could have been said.
    """

    def _repo(self, files):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for rel, body in files.items():
            p = tmp / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)
        return tmp

    def test_a_name_written_out_by_hand_is_reported(self):
        from kernel import foresee
        tmp = self._repo({
            "app/flows.py": "def render_document():\n    pass\n",
            "tests/test_registry.py":
                'ACTIVE = ["render_document", "other_thing"]\n',
        })
        hits = foresee.foresee(tmp, ["app/**"])
        self.assertIn("render_document", hits)
        self.assertEqual(hits["render_document"][0][0], "tests/test_registry.py")

    def test_an_import_is_not_reported(self):
        """`from x import Handler` is a dependency the language checks and a
        rename updates. It breaks loudly and it never caused a widen."""
        from kernel import foresee
        tmp = self._repo({
            "app/flows.py": "def render_document():\n    pass\n",
            "web/view.py": "from app.flows import render_document\n"
                           "render_document()\n",
        })
        self.assertEqual(foresee.foresee(tmp, ["app/**"]), {})

    def test_a_name_the_rest_of_the_repo_also_defines_is_not_a_coupling(self):
        """`repo_root` is declared in twenty files. Renaming yours breaks none
        of them, and reporting it buries the one that matters -- measured, 207
        names down to a handful on one package."""
        from kernel import foresee
        tmp = self._repo({
            "app/a.py": "def repo_root():\n    pass\n",
            "lib/b.py": "def repo_root():\n    pass\n",
            "tests/t.py": 'X = "repo_root"\n',
        })
        self.assertEqual(foresee.foresee(tmp, ["app/**"]), {})

    def test_a_short_name_matches_by_accident_and_is_skipped(self):
        from kernel import foresee
        tmp = self._repo({
            "app/a.py": "def ok():\n    pass\n",
            "tests/t.py": 'X = "ok"\n',
        })
        self.assertEqual(foresee.foresee(tmp, ["app/**"]), {})
        self.assertIn("ok", foresee.foresee(tmp, ["app/**"], min_name=2))


class ArithmeticOverTheWholeLedger(unittest.TestCase):
    """PL-7.  Twenty-five commands and every one answers about a single task.

    The findings that decided anything in the last two reviews were cross-task
    -- how far into a task the first sentence lands, how many claims a file
    brings with it, how many terminal states are a signature rather than a
    checker -- and each was SQL somebody typed once and did not keep. A number
    nobody can re-run is an anecdote.
    """

    def _repo(self):
        from kernel import ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        return ledger.connect(tmp), tmp

    def _task(self, conn, tid, when):
        from kernel import ledger
        ledger.insert(conn, "task", id=tid, request="r", scope_globs=["**"],
                      base_commit="", created_at=when)

    def _mark(self, conn, tid, when, allowed=True, basis=""):
        from kernel import ledger
        payload = {"path": "app/x.py", "allowed": allowed}
        if basis:
            payload["basis"] = basis
        ledger.insert(conn, "event", task_id=tid, claim_id=None, kind="hook_seen",
                      actor="hook", payload=payload, created_at=when)

    def test_it_finds_the_task_that_wrote_before_it_engaged(self):
        """The shape that retired the gate for a whole task, without hand SQL."""
        from kernel import ledger, trend
        conn, _ = self._repo()
        self._task(conn, "t-good", "2026-08-11T10:00:00")
        ledger.insert(conn, "event", task_id="t-good", claim_id=None,
                      kind="engagement", actor="worker", payload={"verdict": "accepted"},
                      created_at="2026-08-11T10:01:00")
        self._mark(conn, "t-good", "2026-08-11T10:02:00", basis="cleared")

        self._task(conn, "t-bad", "2026-08-11T11:00:00")
        self._mark(conn, "t-bad", "2026-08-11T11:01:00", basis="no-claims")
        self._mark(conn, "t-bad", "2026-08-11T11:02:00", basis="no-claims")
        ledger.insert(conn, "event", task_id="t-bad", claim_id=None,
                      kind="engagement", actor="worker", payload={"verdict": "accepted"},
                      created_at="2026-08-11T11:30:00")

        lag = {r["task"]: r["writes_before_first_sentence"]
               for r in trend.engagement_lag(conn)}
        self.assertEqual(lag, {"t-good": 0, "t-bad": 2})

    def test_the_gate_report_separates_the_two_silences(self):
        """`refused=0` means both "nothing was owed" and "nothing was visible".

        Counting refusals cannot tell them apart; counting bases can, which is
        the whole reason `basis` exists.
        """
        from kernel import trend
        conn, _ = self._repo()
        self._task(conn, "t-cleared", "2026-08-11T10:00:00")
        self._mark(conn, "t-cleared", "2026-08-11T10:01:00", basis="cleared")
        self._task(conn, "t-blind", "2026-08-11T11:00:00")
        self._mark(conn, "t-blind", "2026-08-11T11:01:00", basis="no-claims")

        by = {r["task"]: r for r in trend.gate(conn)}
        self.assertEqual(by["t-cleared"]["refused"], 0)
        self.assertEqual(by["t-blind"]["refused"], 0)      # identical here
        self.assertEqual(by["t-cleared"]["bases"], {"cleared": 1})
        self.assertEqual(by["t-blind"]["bases"], {"no-claims": 1})

    def test_green_and_signed_are_counted_apart(self):
        """Both are terminal and only one is evidence."""
        from kernel import ledger, trend
        conn, tmp = self._repo()
        self._task(conn, "t-1", "2026-08-11T10:00:00")
        for cid, kind in (("c1", "secret"), ("c2", "fail-closed")):
            ledger.insert(conn, "claim", id=cid, task_id="t-1", kind=kind,
                          question="q", subject_refs="[]", checker=kind,
                          origin="derive", created_at="2026-08-11T10:00:01")
        ledger.append_attempt(conn, claim_id="c1", exit_code=0, stdout="", stderr="",
                              argv="[]", duration_ms=5, subject_digest="{}",
                              checker_sha="x", config_sha="y", worktree="/",
                              head_commit="h", started_at="n", ended_at="n")
        ledger.append_attempt(conn, claim_id="c2", exit_code=1, stdout="", stderr="",
                              argv="[]", duration_ms=5, subject_digest="{}",
                              checker_sha="x", config_sha="y", worktree="/",
                              head_commit="h", started_at="n", ended_at="n")
        ledger.insert(conn, "accepted_risk", claim_id="c2", kind="baseline_raise",
                      who="someone", why="x" * 60, was_tty=0,
                      git_record=".v4/risks/c2.json", subject_digest="d",
                      created_at="2026-08-11T10:05:00", scope="task", cover_key="")

        # A claim with no attempt at all. `RETRACTED` is terminal and is
        # reached by one event, so `last` is NULL and this counted as open
        # forever -- in the report that exists to say how work *ended*.
        ledger.insert(conn, "claim", id="c3", task_id="t-1", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026-08-11T10:02:00")
        ledger.insert(conn, "event", task_id="t-1", claim_id="c3",
                      kind="retracted", actor="kernel", payload={},
                      created_at="2026-08-11T10:03:00")

        by_kind = trend.how_claims_ended(conn)[0]["by_kind"]
        self.assertEqual(by_kind["secret"]["green"], 1)
        self.assertEqual(by_kind["secret"]["signed"], 0)
        self.assertEqual(by_kind["fail-closed"]["signed"] +
                         by_kind["fail-closed"]["lapsed"], 1,
                         "signed, or signed-and-since-lapsed, but not open")
        self.assertEqual(by_kind["fail-closed"]["green"], 0)
        self.assertEqual(by_kind["lint"],
                         {"green": 0, "signed": 0, "open": 0,
                          "retracted": 1, "lapsed": 0},
                         "a retracted claim ended; it is not still open")

    def test_it_answers_the_same_way_twice(self):
        """No agent, no sampling. The point of the whole module."""
        from kernel import trend
        conn, _ = self._repo()
        self._task(conn, "t-1", "2026-08-11T10:00:00")
        self._mark(conn, "t-1", "2026-08-11T10:01:00", basis="cleared")
        self.assertEqual(trend.report(conn), trend.report(conn))


class WhatDidThisTaskCost(unittest.TestCase):
    """`cost_observation.task_id` was written as None on every row.

    980 rows on one adopter, the column entirely empty, while the function
    writing them held the claim and the claim names its task. Nobody decided
    that: the value was one query away and the query was never written, so the
    question the table exists for had no answer while its rows accumulated.

    `tokens` staying empty is a different thing and not a defect. `v4 cost
    record` writes it, marked `self_reported` because a number an agent reports
    about itself cannot be used to judge that agent -- so an empty column there
    means nobody ran the command, not that nothing can.
    """

    def test_a_cost_row_names_the_task_it_belongs_to(self):
        from kernel import ledger, runner
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t-1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t-1", kind="secret",
                      question="q", subject_refs="[]", checker="secret",
                      origin="derive", created_at="2026")

        class _R:
            exit_code, stdout, stderr, argv = 0, "", "", []
            duration_ms, out_payload = 12, None
            subject_digest, checker_sha = "{}", "sha"
        runner.record(conn, "c1", _R(), repo_root=tmp, config_sha="x",
                      worktree="/", staleness_stamp="d")
        row = conn.execute(
            "SELECT task_id, claim_id, tokens FROM cost_observation").fetchone()
        self.assertEqual((row["task_id"], row["claim_id"]), ("t-1", "c1"))
        self.assertIsNone(row["tokens"])          # a checker spends none


class EveryPathUnderV4IsAccountedFor(unittest.TestCase):
    """Four misses, one shape: somebody taught the kernel to write a new file
    and did not add it to the list that says the kernel writes it.

    `.pyc`, then signature records, then `.v4/deferred/`, then `.v4/home` --
    each found by a separate review, each fixed by appending one line, and the
    next one arrived anyway. Appending does not stop it; the list is only
    consulted when somebody remembers it exists.

    So the question is asked from the other end. Every `.v4/` path the kernel
    names has to be in exactly one of two sets: the kernel writes it, or an
    adopter owns it. A fifth path arrives red, and whoever adds it has to say
    which it is -- which is the decision that kept getting skipped.
    """

    def test_no_v4_path_falls_between_the_two_sets(self):
        from kernel.hashing import KERNEL_WRITTEN, ADOPTER_OWNED, SHIPPED_DIRS
        import ast as _ast
        import re

        # Three ways a `.v4/` path can be answered for: by name, by the
        # install manifest's per-file shas, or because a person owns it.
        tree = _ast.parse((REPO / "kernel" / "cli.py").read_text(encoding="utf-8"))
        stamped = set()
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.Call)
                    and getattr(node.func, "attr", "") == "stamp_generated"):
                for arg in node.args:
                    if isinstance(arg, (_ast.List, _ast.Tuple)):
                        stamped |= {e.value for e in arg.elts
                                    if isinstance(e, _ast.Constant)}
        self.assertTrue(stamped, "stamp_generated call not found")

        known = (tuple(KERNEL_WRITTEN) + tuple(ADOPTER_OWNED)
                 + tuple(SHIPPED_DIRS) + tuple(stamped))
        unaccounted = {}
        for path in sorted((REPO / "kernel").rglob("*.py")):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(r'"(\.v4/[^"]*)"', src):
                lit = m.group(1)
                # Prose and globs are not paths this question is about.
                if " " in lit or "*" in lit:
                    continue
                bare = lit.split("{")[0].rstrip("./")      # `.v4/facts.{x}.json`
                if not any(bare.startswith(k.rstrip("/")) or
                           k.rstrip("/").startswith(bare) for k in known):
                    unaccounted[lit] = f"{path.relative_to(REPO)}"
        self.assertEqual(
            unaccounted, {},
            "each of these is either written by the kernel (add it to "
            "KERNEL_WRITTEN, or stamp it) or owned by the adopter (add it to "
            "ADOPTER_OWNED). Leaving it out means a task's diff will report it "
            "as work the worker cannot explain.")

    def test_and_no_tracked_v4_file_falls_between_them_either(self):
        """The same question, asked of what is actually in the repo.

        The scan above reads double-quoted single literals in `kernel/**/*.py`,
        and its own class docstring says the point is that "a fifth path arrives
        red". Two shapes walk past it: a path built by joining two literals is
        invisible, and a template degenerates to a bare directory --
        `config.BASELINE_TEMPLATE` splits at the brace, leaving `.v4/`, which
        every known entry starts with, so all three baselines were answered
        trivially.

        Measured when this was written: 6 of the 20 tracked entries under
        `.v4/` -- `control_plane_budget.json`, three `*_baseline.json`,
        `obligation_catalogue.json` and `risk_rubric.json` -- were in neither
        set, and the test above was green.

        Git is the other end, and it cannot be walked past: a file is tracked or
        it is not.
        """
        import ast as _ast
        import subprocess
        from kernel.hashing import KERNEL_WRITTEN, ADOPTER_OWNED, SHIPPED_DIRS

        # The third route, the same one the case above reads: `stamp_generated`
        # records a sha per file, so those are answered by record rather than by
        # name. The three registries are there and belong in neither list.
        tree = _ast.parse((REPO / "kernel" / "cli.py").read_text(encoding="utf-8"))
        stamped = set()
        for node in _ast.walk(tree):
            if (isinstance(node, _ast.Call)
                    and getattr(node.func, "attr", "") == "stamp_generated"):
                for arg in node.args:
                    if isinstance(arg, (_ast.List, _ast.Tuple)):
                        stamped |= {e.value for e in arg.elts
                                    if isinstance(e, _ast.Constant)}
        self.assertTrue(stamped, "stamp_generated call not found")

        tracked = subprocess.run(["git", "ls-files", ".v4"], cwd=REPO,
                                 capture_output=True, text=True).stdout.split()
        self.assertGreaterEqual(len(tracked), 10, tracked)
        known = (tuple(KERNEL_WRITTEN) + tuple(ADOPTER_OWNED)
                 + tuple(SHIPPED_DIRS) + tuple(stamped))
        unaccounted = [rel for rel in tracked
                       if not any(rel == k.rstrip("/") or rel.startswith(k)
                                  or rel.startswith(k.rstrip("/") + "/")
                                  or k.rstrip("/").startswith(rel)
                                  for k in known)]
        self.assertEqual(
            unaccounted, [],
            "these are in the repo and in neither set: whoever added one has to "
            "say whether the kernel writes it or a person owns it, and that is "
            "the decision this class exists to stop being skipped")


class ATestFileHasToBeAPythonFile(unittest.TestCase):
    """`_is_test` matched anything named `test_*`, extension included or not.

    Measured on a real adoption: the target repo's pytest had collected this
    checker's own bypass fixture and left `__pycache__/test_thing.cpython-312-
    pytest-9.1.0.pyc` beside it. The fixture harness builds subject refs from
    every file in the case; `only` takes the first test file by sort order, and
    that was the `.pyc`. The checker asked its question about a compiled
    artefact, found no expectations in it, and returned 0 -- so the one bypass
    case proving it cannot be walked around was itself walked around.

    The registration gate caught it. The repo it was written in never could:
    nothing there compiles a fixture.
    """

    def t(self, p):
        from kernel.analysis.test_expectation_diff import is_test
        return is_test(p)

    def test_a_python_test_file_counts(self):
        self.assertTrue(self.t("tests/test_thing.py"))
        self.assertTrue(self.t("tests/thing_test.py"))

    def test_a_compiled_artefact_does_not(self):
        self.assertFalse(self.t("__pycache__/test_thing.cpython-312-pytest-9.1.0.pyc"))
        self.assertFalse(self.t("tests/__pycache__/test_x.cpython-311.pyc"))

    def test_data_named_like_a_test_does_not(self):
        self.assertFalse(self.t("tests/fixtures/x/test_data.json"))
        self.assertFalse(self.t("docs/test_plan.md"))

    def test_ordinary_source_does_not(self):
        self.assertFalse(self.t("app/store.py"))


class FrameworkFixturesDoNotLiveInTheAdoptersTests(unittest.TestCase):
    """`tests/` belongs to the adopter, and a red fixture is broken on purpose.

    Measured on a real adoption: `v4 install` put this framework's fixture sets
    at `tests/fixtures/`, the repo's `pytest` walked `tests/`, and collection
    stopped on 78 errors before one of the repo's own tests ran. Everything else
    this framework writes goes under `.v4/` or into a directory it owns; this
    was the one exception, and it put unparsable files where a test runner is
    guaranteed to look.
    """

    def test_a_fixture_set_lands_under_v4(self):
        from kernel import install
        self.assertEqual(install.fixture_dest("tests/fixtures/scope"),
                         ".v4/fixtures/scope")

    def test_the_name_is_kept(self):
        from kernel import install
        self.assertEqual(install.fixture_dest("tests/fixtures/test_token_shape"),
                         ".v4/fixtures/test_token_shape")

    def test_nothing_lands_in_tests(self):
        from kernel import install
        for rel in ("tests/fixtures/a", "tests/fixtures/b/c"):
            self.assertFalse(install.fixture_dest(rel).startswith("tests/"))

    def test_install_puts_them_there(self):
        from kernel import install
        src = Path(tempfile.mkdtemp()); dst = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, src, ignore_errors=True)
        self.addCleanup(shutil.rmtree, dst, ignore_errors=True)
        for d in ("checkers", "detectors", ".v4/lenses", ".claude",
                  "tests/fixtures/probe"):
            (src / d).mkdir(parents=True, exist_ok=True)
        (src / "checkers" / "c.py").write_text("v1\n")
        (src / "tests" / "fixtures" / "probe" / "red.py").write_text("def broken(:\n")
        (src / ".v4" / "checkers.json").write_text(json.dumps(
            {"c": {"path": "checkers/c.py", "fixtures": "tests/fixtures/probe",
                   "kinds": ["k"], "sha256": "", "reads": ["**"]}}))
        (src / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"k": {"checker": "c", "question_template": "q"}}))
        (src / ".v4" / "detectors.json").write_text("{}")
        (src / ".claude" / "settings.template.json").write_text("{}")
        install.copy_files(src, dst, {"k": {"checker": "c",
                                            "question_template": "q"}})
        self.assertTrue((dst / ".v4" / "fixtures" / "probe" / "red.py").is_file())
        self.assertFalse((dst / "tests").exists())


class OneAnswerToWhichFilesThisRepoOwns(unittest.TestCase):
    """`_files_in_scope` walked the tree; the digest asked git. Two answers.

    Measured on the reference adopter before the fix: 94,147 paths in 4,484ms
    from the walk against 367ms from git, and nearly the whole difference was
    `.venv/` and `node_modules/`. Detectors were pointed at a vendored library
    that the staleness key covering their claims did not include.
    """

    def _repo(self):
        import subprocess, tempfile
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / "app.py").write_text("x = 1\n")
        (root / ".gitignore").write_text(".venv/\nnode_modules/\n")
        for d in (".venv", "node_modules"):
            (root / d).mkdir()
            (root / d / "vendored.py").write_text("y = 2\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        return root

    def test_an_ignored_directory_is_not_a_file_this_repo_owns(self):
        from kernel import hashing, lifecycle
        root = self._repo()
        files = lifecycle._files_in_scope(root, ["**"])
        self.assertIn("app.py", files)
        for junk in (".venv/vendored.py", "node_modules/vendored.py"):
            self.assertNotIn(junk, files, f"{junk} is git-ignored and was derived over")
        self.assertNotIn(".venv/vendored.py", hashing.tree_state(root))

    def test_the_two_readers_agree(self):
        """The set derived over and the set the key is taken over are one set."""
        from kernel import hashing, lifecycle
        root = self._repo()
        self.assertEqual(set(lifecycle._files_in_scope(root, ["**"])),
                         set(hashing.tree_state(root)))

    def test_a_directory_git_does_not_manage_still_derives(self):
        """Not a repo under judgment -- a fixture dir. Zero claims is not right."""
        import tempfile
        from kernel import lifecycle
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        (root / "a.py").write_text("x = 1\n")
        self.assertEqual(lifecycle._files_in_scope(root, ["**"]), ["a.py"])


class TakenOnceNotCached(unittest.TestCase):
    """`task_report` hands the digest down; nothing memoises it.

    `lifecycle` takes this digest before a checker runs and again after, and
    those two are required to differ when the checker moved the tree -- that
    difference is the whole SUBJECT_MOVED signal. A module-level cache would
    delete it with no test failing.
    """

    def test_a_second_call_sees_a_changed_tree(self):
        import subprocess, tempfile
        from kernel import hashing
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        before = hashing.worktree_digest(root)
        (root / "a.py").write_text("x = 2\n")
        self.assertNotEqual(before, hashing.worktree_digest(root))

    def test_a_report_over_many_claims_asks_git_once(self):
        """367ms per call on the reference adopter; twenty repo-scoped claims
        spent seven seconds asking the same question twenty times."""
        import tempfile
        from unittest import mock
        from kernel import ledger, state, hashing
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for i in range(5):
            ledger.insert(conn, "claim", id=f"c{i}", task_id="t", kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin="derive", created_at="2026")
            ledger.append_attempt(conn, claim_id=f"c{i}", exit_code=0, stdout="",
                                  stderr="", argv="[]", duration_ms=1,
                                  subject_digest="{}", checker_sha="k",
                                  config_sha="c", worktree="/", head_commit="d",
                                  started_at="n", ended_at="n")
        real = hashing.worktree_digest
        with mock.patch.object(hashing, "worktree_digest",
                               side_effect=real) as spy:
            state.task_report(conn, root, "t",
                              kinds_cfg={"lint": {"staleness": "repo"}},
                              config_sha="c", checker_sha_of=lambda _: "k")
        self.assertEqual(spy.call_count, 1, "one report, one question to git")


class ExportIsTheWholeChainOrItIsNotAChain(unittest.TestCase):
    """`v4 export --task` raised OperationalError on every call it ever got:
    `WHERE task_id = ?` against `task`, which has no such column. And a repaired
    version would still be wrong -- `verify_exported` walks `prev_hash` from
    GENESIS, so one task's slice reports a removed row on every row."""

    def _ledger(self):
        import tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        conn = ledger.connect(root)
        for i, t in enumerate(("t-one", "t-two")):
            ledger.insert(conn, "task", id=t, request="r", scope_globs=["**"],
                          base_commit="", created_at="2026")
            ledger.insert(conn, "claim", id=f"c{i}", task_id=t, kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin="derive", created_at="2026")
            ledger.append_attempt(conn, claim_id=f"c{i}", exit_code=0, stdout="",
                                  stderr="", argv="[]", duration_ms=1,
                                  subject_digest="{}", checker_sha="x",
                                  config_sha="y", worktree="/", head_commit="d",
                                  started_at="n", ended_at="n")
        return root, conn

    def test_the_export_takes_no_task_and_verifies(self):
        from kernel import ledger
        root, conn = self._ledger()
        out = root / "export.jsonl"
        ledger.export_jsonl(conn, out, root)
        n, problems = ledger.verify_exported(out)
        self.assertEqual((n, problems), (2, []))

    def test_the_flag_that_could_not_work_is_gone(self):
        from kernel import ledger
        import inspect
        self.assertNotIn("task_id", inspect.signature(ledger.export_jsonl).parameters)

    def test_an_edited_row_in_the_export_is_reported(self):
        """The export is what CI walks -- the ledger is not cloned.

        `verify_exported` had only a clean-path test, so replacing its whole
        body with `return len(attempts), []` left the suite green and turned the
        CI step "Walk the exported chain" into one that can never fail. This
        edits a row the way somebody covering a verdict would.
        """
        from kernel import ledger
        root, conn = self._ledger()
        out = root / "export.jsonl"
        ledger.export_jsonl(conn, out, root)
        lines = out.read_text().splitlines()
        for i, line in enumerate(lines):
            row = json.loads(line)
            if row.get("table") == "attempt" or "exit_code" in str(row):
                edited = line.replace('"exit_code": 0', '"exit_code": 1') \
                             .replace('"exit_code":0', '"exit_code":1')
                if edited != line:
                    lines[i] = edited
                    break
        else:
            self.fail("no attempt row in the export to edit")
        out.write_text("\n".join(lines) + "\n")
        n, problems = ledger.verify_exported(out)
        self.assertTrue(
            problems,
            "a row was edited in the exported chain and the walk reported "
            "nothing, so the CI step that walks it cannot fail")

    def test_a_removed_row_in_the_export_is_reported(self):
        """Truncation is the other way to make a verdict disappear."""
        from kernel import ledger
        root, conn = self._ledger()
        out = root / "export.jsonl"
        ledger.export_jsonl(conn, out, root)
        lines = [l for l in out.read_text().splitlines() if l.strip()]
        keep = [l for l in lines if '"claim_id": "c0"' not in l
                and '"claim_id":"c0"' not in l]
        self.assertLess(len(keep), len(lines), "nothing was removed")
        out.write_text("\n".join(keep) + "\n")
        n, problems = ledger.verify_exported(out)
        self.assertTrue(problems, "an attempt was removed and the walk was silent")


class EveryDeclaredKindHasSomethingThatRaisesIt(unittest.TestCase):
    """A checker nothing can raise a claim for never runs, and looks installed.

    Found by asking the ledger rather than the source. Six kinds were declared,
    their checkers were written, registered, and named in `claim_kinds.json`,
    and no detector emitted them -- so across the reference adopter's 36 tasks
    each appeared exactly zero times:

        dal-write  design-pins  signature-change  webhook-replay
        surface-proof  runtime-proof

    `v4 doctor` counts registered checkers and `v4 ship` prints which detectors
    ran; neither asks whether a declared kind is reachable, because both start
    from the artefacts that exist rather than from the ones that were promised.
    """

    ROOT = Path(__file__).resolve().parent.parent

    #: Raised by a command, not by a detector. `v4 review raise` inserts it.
    #: The one legitimate exception, and it is written here so that adding a
    #: second one is a decision somebody makes rather than a test that stops
    #: covering things.
    RAISED_BY_A_COMMAND = {"review-finding"}

    def test_no_kind_is_declared_with_nothing_to_raise_it(self):
        kinds = json.loads((self.ROOT / ".v4" / "claim_kinds.json")
                           .read_text(encoding="utf-8"))
        orphans = []
        for name, spec in sorted(kinds.items()):
            if name in self.RAISED_BY_A_COMMAND:
                continue
            det = spec.get("detector")
            if not det:
                orphans.append(f"{name}: no detector")
            elif not (self.ROOT / "detectors" / det).is_file():
                orphans.append(f"{name}: names {det}, which is not on disk")
        self.assertEqual(orphans, [], "these checkers can never run:\n  "
                                      + "\n  ".join(orphans))

    def test_every_detector_on_disk_is_named_by_a_kind(self):
        """The other direction. A detector nothing names still runs -- `derive`
        globs the directory -- and its claims land under a kind with no checker
        behind it, which is the same hole pointed the other way."""
        kinds = json.loads((self.ROOT / ".v4" / "claim_kinds.json")
                           .read_text(encoding="utf-8"))
        named = {v.get("detector") for v in kinds.values() if v.get("detector")}
        on_disk = {p.name for p in (self.ROOT / "detectors").glob("*.py")
                   if not p.name.startswith("_")}
        self.assertEqual(sorted(on_disk - named), [])


class ADetectorsSubjectCarriesWhatACheckersDoes(unittest.TestCase):
    """`run_detector` built a subject with two fields hard-coded empty.

    `diff_base` was `""` and `params` was `{}`, while `verify_checker` had
    passed `HEAD` and the kernel had put `derive_exclude` in a checker's params
    the whole time. Both were invisible while every detector only looked at the
    `subject_files` the kernel had already filtered, and both cost the moment
    one swept past them:

      diff_base       `test-weakened` raised 0 claims across 36 tasks, and its
                      own registration gate could not have failed -- every red
                      case of a diff-dependent detector emitted nothing.
      derive_exclude  a repo-wide `test-shape` reported 17 findings inside
                      `tests/fixtures/`, each one a red fixture doing its job.
    """

    def _subject_of(self, **kw):
        """The payload `run_detector` hands the detector, without running one."""
        import tempfile
        from kernel import detector_protocol
        seen = {}

        # Patched at `runner._run_contained`, which is what `run_detector`
        # calls -- it used `subprocess.run` directly, the shape
        # `_run_contained` was written to replace, and this spy sat on that.
        # The seam moved; what is being asserted did not.
        from kernel import runner

        def spy(argv, cwd, timeout_sec, tmpdir):
            i = argv.index("--subject")
            seen.update(json.loads(Path(argv[i + 1]).read_text()))
            return 0, "", "", 0

        with tempfile.TemporaryDirectory() as td:
            with unittest.mock.patch.object(runner, "_run_contained", spy):
                detector_protocol.run_detector(Path(td), Path(td) / "d.py", [], **kw)
        return seen

    def test_the_diff_base_reaches_the_detector(self):
        self.assertEqual(self._subject_of(diff_base="abc123")["diff_base"], "abc123")

    def test_derive_exclude_reaches_the_detector(self):
        s = self._subject_of(params={"derive_exclude": ["tests/fixtures/**"]})
        self.assertEqual(s["params"], {"derive_exclude": ["tests/fixtures/**"]})

    def test_derive_passes_both(self):
        """Not just that the parameters exist -- that the one caller uses them.

        The first version of this read `kernel/derive.py` and asserted that the
        strings `diff_base=base` and `derive_exclude` appeared near the call.
        `test-shape` raised it the same hour it was written, and it was right: a
        test that reads source text passes whatever the code does. Rename the
        local `base` and it stays green while the detector goes blind again.

        So it runs `derive` and reads what arrived.
        """
        import subprocess, tempfile
        from kernel import (config, derive as derive_mod,
                            detector_protocol, ledger)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "derive_exclude": ["tests/fixtures/**"]}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / "detectors").mkdir()
        (tmp / "detectors" / "always_probe.py").write_text("")
        conn = ledger.connect(tmp)
        cfg = config.RepoConfig(tmp)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="the-base", created_at="2026")

        got = {}

        def spy(root, det, files, facts=None, **kw):
            got.update(kw)
            # The shape `run_detector` returns. `DetectorRun` and not a bare
            # tuple, so this stand-in stays honest about the contract rather
            # than about the number of elements it had on the day it was
            # written -- which is what needed changing here when `duration_ms`
            # was added.
            return detector_protocol.DetectorRun(0, "", "", None, 0)

        with unittest.mock.patch.object(derive_mod, "run_detector", spy):
            derive_mod.derive(conn, cfg, task_id="t", scope_globs=["**"],
                              subject_files=[], phase="open")
        self.assertEqual(got.get("diff_base"), "the-base")
        self.assertEqual((got.get("params") or {}).get("derive_exclude"),
                         ["tests/fixtures/**"])


class StatusHasAShapeThatDoesNotMove(unittest.TestCase):
    """An orchestrator had one machine-readable thing here: the exit code.

    Which claim, in which state, and what is blocking had to be recovered by
    parsing lines written for a person -- lines that change whenever the
    wording improves. `--json` carries the same facts and the same exit code.
    """

    def _repo(self):
        import subprocess, tempfile
        from kernel import ledger
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"lint": {"checker": "lint", "staleness": "repo",
                      "question_template": "q"}}))
        conn = ledger.connect(tmp)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026")
        return tmp

    def _run(self, tmp, *extra):
        import subprocess, sys
        r = subprocess.run(
            [sys.executable, "-m", "kernel.cli", "--repo", str(tmp),
             "status", "--task", "t", *extra],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True)
        return r

    def test_a_finding_carries_what_it_says(self):
        """`note` is the finding. Without it an orchestrator handed thirty of
        them has coordinates and no way to cut tasks by what they are.

        Written at insert. The first version of this test used an UPDATE and
        the ledger's own trigger refused it -- `ledger is append-only: claim`,
        which is the guard doing exactly its job."""
        from kernel import ledger
        tmp = self._repo()
        conn = ledger.connect(tmp)
        ledger.insert(conn, "claim", id="c2", task_id="t", kind="review-finding",
                      question="q", subject_refs="[]", checker="review-finding",
                      origin="review", created_at="2026",
                      file="app.py", symbol="save", variant="electrification",
                      note="the lock is taken after the read")
        got = [c for c in json.loads(self._run(tmp, "--json").stdout)["claims"]
               if c["id"] == "c2"][0]
        self.assertEqual(got["note"], "the lock is taken after the read")
        self.assertEqual(got["variant"], "electrification")
        self.assertEqual((got["file"], got["symbol"]), ("app.py", "save"))

    def test_the_json_carries_the_open_claims(self):
        tmp = self._repo()
        r = self._run(tmp, "--json")
        got = json.loads(r.stdout)
        self.assertEqual(got["open"], ["c1"])
        self.assertEqual(got["claims"][0]["state"], "OPEN")
        self.assertEqual((got["terminal"], got["total"]), (0, 1))

    def test_the_exit_code_is_the_one_it_always_was(self):
        """The contract that already existed does not move because a flag was
        added: 0 when every claim is terminal, 1 otherwise, in both modes."""
        tmp = self._repo()
        self.assertEqual(self._run(tmp).returncode,
                         self._run(tmp, "--json").returncode)
        self.assertEqual(self._run(tmp, "--json").returncode, 1)


class InstallRepairsAFieldNobodyAnswered(unittest.TestCase):
    """`write_kinds` said "never drop its edits" and did it per kind.

    An existing kind was left untouched forever, so a field the adopter never
    set could never be repaired. Measured on the reference adopter: four kinds
    shipped with `detector: null` back when none of them had a detector, the
    detectors were written and installed, and all four still read `null` --
    four checkers copied into a repo that could not raise a claim for any.
    """

    def _dst(self, existing):
        import tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / ".v4").mkdir()
        (d / ".v4" / "claim_kinds.json").write_text(json.dumps(existing))
        return d

    def test_a_null_detector_is_filled_in(self):
        from kernel import install
        d = self._dst({"dal-write": {"checker": "dal-write", "detector": None}})
        install.write_kinds(d, {"dal-write": {"checker": "dal-write",
                                              "detector": "dal_write.py"}})
        got = json.loads((d / ".v4" / "claim_kinds.json").read_text())
        self.assertEqual(got["dal-write"]["detector"], "dal_write.py")

    def test_a_value_the_repo_chose_is_left_alone(self):
        """The half the old docstring was right about."""
        from kernel import install
        d = self._dst({"lint": {"checker": "lint", "detector": "my_lint.py",
                                "staleness": "subject"}})
        install.write_kinds(d, {"lint": {"checker": "lint",
                                         "detector": "always_lint.py",
                                         "staleness": "repo"}})
        got = json.loads((d / ".v4" / "claim_kinds.json").read_text())
        self.assertEqual(got["lint"]["detector"], "my_lint.py")
        self.assertEqual(got["lint"]["staleness"], "subject")

    def test_a_kind_the_repo_does_not_have_arrives_whole(self):
        from kernel import install
        d = self._dst({})
        install.write_kinds(d, {"new-kind": {"checker": "c", "detector": "d.py"}})
        got = json.loads((d / ".v4" / "claim_kinds.json").read_text())
        self.assertEqual(got["new-kind"], {"checker": "c", "detector": "d.py"})


class AProofIsAboutTheRunThatMadeIt(unittest.TestCase):
    """`runtime-proof` accepted a trigger that did nothing.

    Demonstrated: `trigger: "true"` against a store an earlier run had left
    populated -- exit 0, "the truth owner agreed", not one byte written.

    The first fix asked the query before the trigger and refused when the
    expectation already held. That is the same idea and it cannot work: the
    answer then depends on what the previous run left behind, so the same bytes
    give two verdicts. The registration gate refused it on exactly that, and a
    correctly-written real proof (`created_at > now() - interval '5 minutes'`)
    would have failed on its second run inside the window.

    A fresh id per invocation has neither problem.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def _run(self, cfg: dict):
        import subprocess, sys, tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / ".v4").mkdir()
        (d / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk", **cfg}))
        subj = d / "s.json"
        subj.write_text(json.dumps({"repo_root": str(d), "subject_refs": []}))
        r = subprocess.run(
            [sys.executable, str(self.ROOT / "checkers" / "runtime_proof.py"),
             "--subject", str(subj)], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    TRUTH = 'python3 -c "import sys; exec(sys.stdin.read())"'
    READ = "print(open('s_{run_id}').read().strip() "\
           "if __import__('os').path.exists('s_{run_id}') else '')"

    def test_a_query_that_cannot_name_this_run_is_refused(self):
        code, out = self._run({
            "truth_command": self.TRUTH,
            "runtime_proof": [{"name": "n", "trigger": "true",
                               "truth": "print('ok')", "expect": "contains:ok"}]})
        self.assertEqual(code, 1)
        self.assertIn("{run_id}", out)

    def test_a_trigger_that_leaves_the_row_passes(self):
        code, _ = self._run({
            "truth_command": self.TRUTH,
            "runtime_proof": [{
                "name": "n",
                "trigger": '''python3 -c "open('s_{run_id}','w').write('ok')"''',
                "truth": self.READ, "expect": "contains:ok"}]})
        self.assertEqual(code, 0)

    def test_the_same_declaration_answers_the_same_way_twice(self):
        """What the pre-trigger version could not do."""
        cfg = {"truth_command": self.TRUTH,
               "runtime_proof": [{
                   "name": "n",
                   "trigger": '''python3 -c "open('s_{run_id}','w').write('ok')"''',
                   "truth": self.READ, "expect": "contains:ok"}]}
        self.assertEqual(self._run(cfg)[0], self._run(cfg)[0])

    def test_a_no_op_trigger_cannot_reach_a_fresh_id(self):
        code, out = self._run({
            "truth_command": self.TRUTH,
            "runtime_proof": [{"name": "n", "trigger": "true",
                               "truth": self.READ, "expect": "contains:ok"}]})
        self.assertEqual(code, 1)


class AnUnansweredFieldIsNotAFailingRepo(unittest.TestCase):
    """`v4 init` writes `TODO` for what only the repo can answer, and three
    checkers take a command out of that file and run it. `test` ran the
    sentinel: `TODO exited 127`, reported as exit 1 -- this repo's tests fail,
    about a repo that never said what its tests are."""

    ROOT = Path(__file__).resolve().parent.parent

    def _test_checker(self, test_command):
        import subprocess, sys, tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / ".v4").mkdir()
        (d / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": test_command, "policy": "allow_accepted_risk"}))
        subj = d / "s.json"
        subj.write_text(json.dumps({"repo_root": str(d), "subject_refs": [],
                                    "params": {}}))
        return subprocess.run(
            [sys.executable, str(self.ROOT / "checkers" / "test.py"),
             "--subject", str(subj)], capture_output=True, text=True).returncode

    def test_the_sentinel_is_unsupported_not_failed(self):
        from kernel import config as config_mod
        self.assertEqual(self._test_checker(config_mod.UNANSWERED), 4)

    def test_a_real_command_still_answers(self):
        self.assertEqual(self._test_checker('echo "3 passed"'), 0)

    def test_declared_reads_absent_empty_and_sentinel_the_same_way(self):
        from kernel import config as config_mod
        for v in (None, "", "   ", config_mod.UNANSWERED, [], {}):
            self.assertIsNone(config_mod.declared({"k": v}, "k"), v)
        self.assertEqual(config_mod.declared({"k": "pytest -q"}, "k"), "pytest -q")


class ASymbolReferenceIsNotAGlob(unittest.TestCase):
    """`public_routes` was in the narrowing guard's key list.

    Its values are `file::symbol` -- FACTS.md calls them "this handler is
    deliberately unauthenticated" -- so `fnmatch(path, "a/b.py::f")` is false
    for every file that exists and every entry read as dead. Measured on the
    reference adopter: two live exemptions, both symbols present in the file
    they name, refusing seven detectors on every task.

    The same mistake as `protected_paths`, one key over, and the comment
    describing that one sits four lines above the list this was in.
    """

    def test_public_routes_is_not_treated_as_a_glob(self):
        from kernel import derive
        self.assertNotIn("public_routes", derive.FILTER_KEYS)

    def test_a_live_symbol_reference_kills_no_detector(self):
        import subprocess, tempfile
        from kernel import derive
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / "routes.py").write_text("def list_brands():\n    return []\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        facts = {"public_routes": ["routes.py::list_brands"],
                 "entrypoint_globs": ["*.py"]}
        self.assertEqual(derive._filters_matching_nothing(root, facts), [])

    def test_a_glob_key_that_matches_nothing_still_speaks_up(self):
        """The guard itself is not weakened -- only the key that is not a glob."""
        import subprocess, tempfile
        from kernel import derive
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / "routes.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        self.assertEqual(derive._filters_matching_nothing(
            root, {"ui_globs": ["web/**"]}), ["ui_globs: web/**"])


class AnAbandonedTaskDoesNotBlockAnything(unittest.TestCase):
    """`doctor` escalated `unanswerable kinds` to BAD on the newest task by
    rowid. That is not the same as an open one: `abandon` exists to give a task
    a second ending, and a probe abandoned with a written reason went on
    escalating the line for as long as nothing newer was opened."""

    def _repo(self):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        # The registry carries `secret` because the claim below is one, and a
        # repo cannot raise a kind it does not register. It was `{}` here --
        # scaffolding, not the thing under test -- until `doctor` started
        # dropping kinds the registry no longer has, at which point an empty
        # registry meant this fixture's claim was reported as history.
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"secret": {"checker": "secret", "question_template": "q"}}))
        (root / ".v4" / "checkers.json").write_text("{}")
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t-probe", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t-probe", kind="secret",
                      question="q", subject_refs="[]", checker="secret",
                      origin="derive", created_at="2026")
        ledger.append_attempt(conn, claim_id="c1", exit_code=4, stdout="", stderr="",
                              argv="[]", duration_ms=1, subject_digest="{}",
                              checker_sha="x", config_sha="y", worktree="/",
                              head_commit="d", started_at="n", ended_at="n")
        return root, conn

    def _line(self, root):
        from kernel import doctor
        return next(r for r in doctor.run(root) if r["what"] == "unanswerable kinds")

    def test_an_open_task_with_an_unanswerable_claim_is_bad(self):
        root, _ = self._repo()
        self.assertEqual(self._line(root)["status"], "bad")

    def test_abandoning_it_leaves_the_report_and_drops_the_escalation(self):
        from kernel import ledger
        root, conn = self._repo()
        ledger.insert(conn, "event", task_id="t-probe", claim_id=None,
                      kind="abandoned", actor="person",
                      payload=json.dumps({"why": "a probe, ended on the record"}),
                      created_at="2026")
        row = self._line(root)
        self.assertEqual(row["status"], "warn")
        # Still named. What was found does not stop being true.
        self.assertIn("t-probe", row["detail"])


class BeingToldTheKeyIsNotBeingToldTheShape(unittest.TestCase):
    """Both live-proof checkers named the key they wanted and stopped there.

    Measured: turning the forcing function on for one adopter needed 277 lines
    of hand-written prose, and roughly 160 of them were the object's shape,
    what `{run_id}` is, and which forms `expect` takes -- none of which is that
    repo's knowledge. A JSON shape assumes no stack, so it belongs here rather
    than in every adopter's docs directory.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def _say(self, checker, cfg):
        import subprocess, sys, tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / ".v4").mkdir()
        (d / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk", **cfg}))
        subj = d / "s.json"
        subj.write_text(json.dumps({"repo_root": str(d), "subject_refs": [],
                                    "params": {}}))
        r = subprocess.run(
            [sys.executable, str(self.ROOT / "checkers" / f"{checker}.py"),
             "--subject", str(subj)], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_runtime_proof_shows_the_object_and_the_run_id(self):
        code, said = self._say("runtime_proof", {})
        self.assertEqual(code, 4)
        for owed in ('"trigger"', '"truth"', '"expect"', "{run_id}",
                     "truth_command", "gt:N", "contains:"):
            self.assertIn(owed, said, owed)

    def test_surface_proof_shows_both_keys_and_why_it_is_not_guessed(self):
        code, said = self._say("surface_proof", {})
        self.assertEqual(code, 4)
        self.assertIn('"surface_command"', said)
        self.assertIn('"surface_cwd"', said)
        self.assertIn("not guessed", said)

    def test_the_ui_globs_hint_reaches_someone(self):
        """It was written into the checker and was unreachable: the detector
        only raised when a command was already declared, so the branch that
        says "you have a surface and have not said how to drive it" could not
        run. The forcing function is what made it reachable."""
        import subprocess, sys, tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / ".v4").mkdir()
        (d / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (d / ".v4" / "facts.probe.json").write_text(json.dumps(
            {"ui_globs": ["web/**"]}))
        subj = d / "s.json"
        subj.write_text(json.dumps({"repo_root": str(d), "subject_refs": [],
                                    "params": {}}))
        out = subprocess.run(
            [sys.executable, str(self.ROOT / "checkers" / "surface_proof.py"),
             "--subject", str(subj)], capture_output=True, text=True)
        self.assertIn("ui_globs", out.stdout + out.stderr)


class ALensFileNobodyCanReadIsALensNobodyRuns(unittest.TestCase):
    """`lenses()` was a dict comprehension over a bare `json.loads`, and
    `lens_brief` indexed `name`, `source`, `checks` and `anti_patterns` out of
    whatever came back.

    So one bad file took `v4 review lens` down with a traceback -- including
    the list, so the other eleven became unreachable -- while `v4 doctor` said
    nothing and exited 0. Layer 3 is the one layer with no program behind it,
    which makes it the last place that should fail silently.
    """

    def _root(self, files: dict):
        import tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / ".v4" / "lenses").mkdir(parents=True)
        for name, body in files.items():
            (d / ".v4" / "lenses" / f"{name}.json").write_text(body)
        return d

    GOOD = json.dumps({"name": "n", "source": "s", "checks": ["look at this"],
                       "anti_patterns": []})

    def test_a_bad_file_does_not_hide_the_good_ones(self):
        from kernel import review
        good, bad = review.lens_files(self._root(
            {"ok": self.GOOD, "broken": "this is not json"}))
        self.assertEqual(list(good), ["ok"])
        self.assertIn("broken", bad)

    def test_every_way_it_used_to_raise(self):
        from kernel import review
        for name, body in (
                ("no_json", "not json"),
                ("no_keys", json.dumps({"name": "n"})),
                ("empty_checks", json.dumps({"name": "n", "source": "s",
                                             "checks": [], "anti_patterns": []})),
                ("not_an_object", json.dumps(["a", "list"]))):
            good, bad = review.lens_files(self._root({name: body}))
            self.assertEqual(good, {}, name)
            self.assertIn(name, bad, name)
            self.assertTrue(bad[name], f"{name}: skipped with no reason")

    def test_a_string_check_is_fine(self):
        """Seven of the eleven hold strings; `check_text` handles both shapes."""
        from kernel import review
        good, _ = review.lens_files(self._root({"ok": self.GOOD}))
        self.assertEqual(review.check_text(good["ok"]["checks"][0]),
                         "look at this")

    def test_doctor_says_so(self):
        from kernel import doctor
        d = self._root({"ok": self.GOOD, "broken": "not json"})
        (d / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        row = next((r for r in doctor.run(d) if r["what"] == "lenses"), None)
        self.assertIsNotNone(row, "doctor never mentions lenses")
        self.assertEqual(row["status"], "bad")
        self.assertIn("broken", row["detail"])

    def test_the_slug_field_is_gone(self):
        """Read by nothing: every caller keys on the filename. Four of eleven
        carried one, so renaming a file made the two disagree with no reader to
        notice -- one fact in two places, which is the shape this repo removes."""
        root = Path(__file__).resolve().parent.parent
        for p in sorted((root / ".v4" / "lenses").glob("*.json")):
            self.assertNotIn("slug", json.loads(p.read_text(encoding="utf-8")),
                             p.name)


class SubjectMovedSaysWhatMoved(unittest.TestCase):
    """It said the tree changed and stopped there.

    Which sends the reader looking for another worktree, when the usual answer
    is that the checker did it to itself. Measured twice here: `test` writing
    `__pycache__/*.pyc` into a repo with no `.gitignore` returned SUBJECT_MOVED
    on every first run, and a `runtime_proof` trigger that wrote its probe file
    into the repo did the same within minutes of being written.

    `worktree_digest` already states the rule -- "a staleness key that the act
    of answering moves is not a staleness key" -- so naming the path is the
    difference between a rule somebody can follow and a riddle.
    """

    def test_a_new_file_is_named(self):
        from kernel import hashing
        self.assertEqual(hashing.moved({"a": "1"}, {"a": "1", "b": "2"}), ["+ b"])

    def test_a_deletion_and_an_edit_read_differently(self):
        from kernel import hashing
        self.assertEqual(
            hashing.moved({"a": "1", "b": "2"}, {"a": "9"}), ["- b", "~ a"])

    def test_nothing_moved_says_nothing(self):
        from kernel import hashing
        self.assertEqual(hashing.moved({"a": "1"}, {"a": "1"}), [])

    def test_a_long_list_is_capped_and_says_so(self):
        """A checker that writes a hundred files should not print a hundred
        lines; the first few name the shape, and the count says there is more."""
        from kernel import hashing
        out = hashing.moved({}, {str(i): "x" for i in range(20)}, limit=3)
        self.assertEqual(len(out), 4)
        self.assertIn("17 more", out[-1])

    def test_the_checker_that_writes_into_the_repo_is_named(self):
        """End to end: the state before, a file the run created, the diagnosis."""
        import subprocess, tempfile
        from kernel import hashing
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        before = hashing.tree_state(root)
        (root / "probe_rt123").write_text("ok")      # what a trigger would leave
        self.assertEqual(hashing.moved(before, hashing.tree_state(root)),
                         ["+ probe_rt123"])


class ASweepAccountsForTheFindingsItSaysItRaised(unittest.TestCase):
    """Both counts were stored and nothing compared them.

    `findings` is what the sweep says it raised; `raised_since_last_sweep` is
    what the ledger saw. Measured on the reference adopter: 61 reported, 1 in
    the ledger, and that stayed true for three days because no command had to
    say anything about the gap.

    Not a refusal to differ -- a sweep may raise five and put the rest in a
    document somebody reads. That is a decision, and this asks for it in
    writing.
    """

    FLOOR = 40

    def _conn(self, findings_in_ledger=0):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="repo-review", request="r",
                      scope_globs=["**"], base_commit="", created_at="2026")
        for i in range(findings_in_ledger):
            ledger.insert(conn, "claim", id=f"f{i}", task_id="repo-review",
                          kind="review-finding", question="q", subject_refs="[]",
                          checker="review-finding", origin="review",
                          created_at="2026")
        return conn

    def test_counts_that_agree_need_no_explanation(self):
        from kernel import sweep
        self.assertEqual(sweep.reconcile(self._conn(3), 3, "", self.FLOOR), "")

    def test_a_gap_with_no_note_is_refused_and_names_the_number(self):
        from kernel import sweep
        why = sweep.reconcile(self._conn(1), 61, "", self.FLOOR)
        self.assertTrue(why)
        self.assertIn("61", why)
        self.assertIn("1", why)
        self.assertIn("60", why)          # where the other 60 went

    def test_a_short_note_does_not_count_as_an_explanation(self):
        from kernel import sweep
        self.assertTrue(sweep.reconcile(self._conn(1), 61, "later", self.FLOOR))

    def test_a_written_reason_lets_the_gap_stand(self):
        from kernel import sweep
        note = ("the other 60 are in docs/reviews/SWEEP.md because none of them "
                "names a symbol a red-green test could close")
        self.assertEqual(sweep.reconcile(self._conn(1), 61, note, self.FLOOR), "")

    def test_a_sweep_that_reports_no_count_is_not_asked(self):
        """`--findings` is optional and was before this; a sweep that says
        nothing about how many it raised is not claiming anything to reconcile."""
        from kernel import sweep
        self.assertEqual(sweep.reconcile(self._conn(5), None, "", self.FLOOR), "")


class TheReviewLineCountsReviewFindings(unittest.TestCase):
    """It counted every open claim on the review task and called them findings.

    The review row is where a task-less finding hangs, and anything that runs
    `v4 derive` against it raises the whole battery there too. Measured here:
    32 open claims on it, 3 of them findings and 29 of them `fail-closed`,
    `test`, `lint` and the rest -- reported as "32 raised by a review", which
    is one output standing for two different facts.
    """

    def _repo(self, kinds):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id=ledger.REVIEW_TASK, request="r",
                      scope_globs=["**"], base_commit="", created_at="2026")
        for i, k in enumerate(kinds):
            ledger.insert(conn, "claim", id=f"c{i}", task_id=ledger.REVIEW_TASK,
                          kind=k, question="q", subject_refs="[]", checker=k,
                          origin="review", created_at="2026",
                          file="a.py", symbol="f")
        return root

    def _line(self, root):
        from kernel import doctor
        return next((r for r in doctor.run(root)
                     if r["what"] == "review findings"), None)

    def test_derived_claims_on_the_review_task_are_not_findings(self):
        row = self._line(self._repo(
            ["review-finding", "fail-closed", "test", "lint", "scope"]))
        self.assertIsNotNone(row)
        self.assertTrue(row["detail"].startswith("1 "), row["detail"])

    def test_no_findings_says_nothing(self):
        self.assertIsNone(self._line(self._repo(["test", "lint"])))


class TheReviewRowIsAContainerNotAUnitOfWork(unittest.TestCase):
    """`derive` treated it as a task and raised the whole battery there.

    It has no diff base, its scope is `**` because a finding can be anywhere,
    and nobody writes code under it -- so every claim a detector raises there
    asks "is this change safe" about no change. Measured here: one `v4 derive`
    against it left 32 open claims, 3 findings and 29 of `fail-closed`,
    `test`, `lint` and the rest. Those 29 also spent the task's three
    re-derive rounds, so `ship` on it has refused ever since and cannot be
    recovered -- the ledger is append-only, which is the point.
    """

    def _cfg(self):
        import subprocess, tempfile
        from kernel import config, ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "claim_kinds.json").write_text(json.dumps(
            {"lint": {"checker": "lint", "staleness": "repo",
                      "question_template": "q"}}))
        (root / "detectors").mkdir()
        (root / "detectors" / "always_probe.py").write_text(
            'print("V4-CLAIM: kind=lint symbol=<module>")\n')
        conn = ledger.connect(root)
        return conn, config.RepoConfig(root)

    def test_the_review_row_derives_nothing(self):
        from kernel import derive, ledger
        conn, cfg = self._cfg()
        ledger.insert(conn, "task", id=ledger.REVIEW_TASK, request="r",
                      scope_globs=["**"], base_commit="", created_at="2026")
        res = derive.derive(conn, cfg, task_id=ledger.REVIEW_TASK,
                            scope_globs=["**"], subject_files=[], phase="open")
        self.assertEqual(res["created"], [])
        self.assertEqual(res["detectors_ran"], {})

    def test_it_returns_empty_rather_than_raising(self):
        """`ship` re-derives. A container that derives nothing converges on the
        first round, which is the right answer; an exception would not be."""
        from kernel import derive, ledger
        conn, cfg = self._cfg()
        ledger.insert(conn, "task", id=ledger.REVIEW_TASK, request="r",
                      scope_globs=["**"], base_commit="", created_at="2026")
        res = derive.derive(conn, cfg, task_id=ledger.REVIEW_TASK,
                            scope_globs=["**"], subject_files=[], phase="ship")
        self.assertEqual(sorted(res), ["created", "detectors_ran", "refused",
                                       "retracted", "seen"])

    def test_an_ordinary_task_still_derives(self):
        from kernel import derive, ledger
        conn, cfg = self._cfg()
        ledger.insert(conn, "task", id="t-real", request="r",
                      scope_globs=["**"], base_commit="", created_at="2026")
        res = derive.derive(conn, cfg, task_id="t-real", scope_globs=["**"],
                            subject_files=[], phase="open")
        self.assertEqual(len(res["created"]), 1)




class DeadWiringCarriesStandingDebtLikeItsSiblings(unittest.TestCase):
    """It swept the whole repo, ran on every task, and had no baseline.

    `staleness: repo`, installed into adopters on 2026-08-12, while `lint`,
    `dependency`, `layer-boundary`, `secret-chain`, `test-shape` and
    `test-token-shape` all carried one. Measured on the first adopter it
    reached: seven tables declared by migrations and read by nothing, none of
    them created by the task that had to answer for them -- and a failing claim
    cannot be signed away, so that task could not ship until somebody else's
    seven-year-old tables were dealt with. One task paying everyone's bill,
    which this project decided against by name.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def _run(self, tmp, accepted=None):
        import subprocess, sys
        if accepted is not None:
            (tmp / ".v4" / "dead-wiring_baseline.json").write_text(
                json.dumps({"accepted": accepted}))
        subj = tmp / "s.json"
        subj.write_text(json.dumps({"repo_root": str(tmp), "subject_refs": [],
                                    "params": {}}))
        r = subprocess.run(
            [sys.executable, str(self.ROOT / "checkers" / "dead_wiring.py"),
             "--subject", str(subj), "--out", str(tmp / "out.json")],
            capture_output=True, text=True)
        out = json.loads((tmp / "out.json").read_text()) if (tmp / "out.json").is_file() else {}
        return r.returncode, r.stdout + r.stderr, out

    def _repo(self):
        import subprocess, tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / "kernel").mkdir()
        (tmp / "kernel" / "ledger.py").write_text(
            'SCHEMA = """CREATE TABLE IF NOT EXISTS orphan (id TEXT);"""\n')
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        return tmp

    def test_the_kind_declares_a_baseline_like_the_others(self):
        kinds = json.loads((self.ROOT / ".v4" / "claim_kinds.json")
                           .read_text(encoding="utf-8"))
        self.assertTrue(kinds["dead-wiring"].get("baseline"),
                        "a repo-sweeping delta checker with no baseline makes "
                        "one task answer for every task before it")

    def test_it_still_fails_with_no_baseline(self):
        code, said, out = self._run(self._repo())
        self.assertEqual(code, 1)
        self.assertIn("orphan", said)
        self.assertTrue(out["problems"][0]["id"], "no id to put in a baseline")

    def test_a_baselined_finding_is_carried_not_raised(self):
        tmp = self._repo()
        _, _, out = self._run(tmp)
        ids = [p["id"] for p in out["problems"]]
        # Every one of them: writing the baseline file itself changes the tree,
        # and this checker reads the tree. Forgiving one of two leaves the other
        # red, which says nothing about whether forgiving worked.
        code, said, out2 = self._run(tmp, accepted=ids)
        self.assertEqual(code, 0, said)
        self.assertEqual(out2["problems"], [])
        self.assertEqual(sorted(out2["carried"]), sorted(ids))
        self.assertIn(f"carrying {len(ids)}", said)

    def test_an_entry_matching_nothing_is_named(self):
        """A baseline that keeps forgiving a finding nobody can find only grows."""
        code, said, out = self._run(self._repo(), accepted=["deadbeefdeadbeef"])
        self.assertIn("deadbeefdeadbeef", said)
        self.assertIn("matches nothing", said)


class DeadWiringAsksGitWhichFilesAreTheRepos(unittest.TestCase):
    """It walked the tree with `rglob` and cached nothing.

    Measured on the reference adopter: 23,677 `.py` under `rglob` against 1,696
    the repo owns -- `.venv/` alone is 21,954 -- and four passes each re-reading
    and re-parsing whatever they saw. `code_only` was entered 23,789 times and
    `ast.walk` ran 37 million; the checker's median had reached 60 seconds,
    past the 30-second line that makes the kernel defer a claim as expensive.
    After: 2.3 seconds, same findings.

    The same shape as `_files_in_scope` before it asked git -- one question
    about which files are this repo's, answered twice, one answer wrong.
    """

    def _repo(self):
        import subprocess, tempfile
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / "app.py").write_text("x = 1\n")
        (root / ".gitignore").write_text("vendor/\n")
        (root / "vendor").mkdir()
        for i in range(5):
            (root / "vendor" / f"lib{i}.py").write_text(f"y = {i}\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        return root

    def test_an_ignored_directory_is_not_this_repos_code(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dw", Path(__file__).resolve().parent.parent / "checkers" / "dead_wiring.py")
        dw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dw)
        root = self._repo()
        names = {p.name for p in dw.tracked_files(root)}
        self.assertIn("app.py", names)
        for i in range(5):
            self.assertNotIn(f"lib{i}.py", names,
                             "a git-ignored vendor tree is not this repo's code")

    def test_a_file_is_read_and_parsed_once(self):
        import importlib.util
        from unittest import mock
        spec = importlib.util.spec_from_file_location(
            "dw2", Path(__file__).resolve().parent.parent / "checkers" / "dead_wiring.py")
        dw = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dw)
        root = self._repo()
        real = dw.code_only
        with mock.patch.object(dw, "code_only", side_effect=real) as spy:
            for _ in range(4):                    # four passes, as the checker has
                dw.source_of(root / "app.py")
        self.assertEqual(spy.call_count, 1, "each pass re-parsed the same file")


class ScopeSaysWhichPathsCanNeverLeaveTheDiff(unittest.TestCase):
    """A path committed after the task opened is in its diff forever.

    `base_commit` is written once and the ledger is append-only, so
    `git diff <base>` keeps every commit made since. Widening does not reach it
    either: the path is not outside the scope, it is somebody else's finished
    work sitting between the base and now.

    Measured: a task opened at 50bd7b82, three commits later its `scope` claim
    listed seven paths, two of them protected. The operator read the FAIL,
    fixed nothing -- there was nothing to fix -- ran `check` again, and each
    attempt cost eleven minutes, because forcing the cheap claim green means
    `--all` and `--all` runs the suite. Twice. Both facts needed to stop were
    already known and nothing put them together.
    """

    ROOT = Path(__file__).resolve().parent.parent

    def _repo(self, commit_after=True):
        import subprocess, tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        run = lambda *a: subprocess.run(a, cwd=d, capture_output=True)
        run("git", "init", "-q", ".")
        run("git", "config", "user.email", "a@b")
        run("git", "config", "user.name", "c")
        (d / ".v4").mkdir()
        (d / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk",
             "protected_paths": [".v4/**"]}))
        (d / "app.py").write_text("x = 1\n")
        run("git", "add", "-A"); run("git", "commit", "-qm", "base")
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=d,
                              capture_output=True, text=True).stdout.strip()
        (d / "other.py").write_text("y = 1\n")
        if commit_after:
            run("git", "add", "-A"); run("git", "commit", "-qm", "someone else")
        return d, base

    def _say(self, d, base):
        import subprocess, sys
        (d / "s.json").write_text(json.dumps(
            {"repo_root": str(d), "diff_base": base, "subject_refs": [],
             "params": {"scope_globs": ["app.py", "s.json"], "forbid_globs": [],
                        "derive_exclude": []}}))
        r = subprocess.run(
            [sys.executable, str(self.ROOT / "checkers" / "scope.py"),
             "--subject", str(d / "s.json")], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_a_committed_path_is_named_as_unreachable(self):
        d, base = self._repo(commit_after=True)
        code, said = self._say(d, base)
        self.assertEqual(code, 1)
        self.assertIn("committed after this task opened", said)
        self.assertIn("other.py", said)
        self.assertIn("new task", said)

    def test_an_uncommitted_path_is_not(self):
        """Widening does reach that one, and saying otherwise would send the
        reader to abandon a task they could have finished."""
        d, base = self._repo(commit_after=False)
        code, said = self._say(d, base)
        self.assertEqual(code, 1)
        self.assertNotIn("committed after this task opened", said)


class SigningTheSameKindTwiceIsHistoryNotABrokenChain(unittest.TestCase):
    """A repo-scoped signature writes one file per kind; the ledger holds one
    row per claim. Sign the same kind twice and the second write replaces the
    file, so the first row points at a record naming somebody else's claim.

    And signing twice is what the tool asks for. The message printed at signing
    says the cover lapses the moment the checker changes, and the only answer
    to that is to sign again. Measured on the reference adopter: two signatures
    at 09:19, two more for the same kinds at 16:22, and `chain: BROKEN` from
    then on -- permanently, because the ledger is append-only and one file
    cannot name two claims.
    """

    def _repo(self):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        subprocess.run(["git", "config", "user.email", "a@b"], cwd=root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "c"], cwd=root, capture_output=True)
        (root / ".v4" / "risks" / "repo").mkdir(parents=True)
        conn = ledger.connect(root)
        # `accepted_risk.claim_id` is a foreign key, so the claims have to be
        # there before anything can be signed for them.
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for cid in ("old-claim", "new-claim", "only-claim", "c-surface",
                    "c-runtime"):
            ledger.insert(conn, "claim", id=cid, task_id="t", kind="lint",
                          question="q", subject_refs="[]", checker="lint",
                          origin="derive", created_at="2026")
        return root, conn

    def _sign(self, root, conn, claim_id, kind, *, write=True):
        from kernel import ledger
        rec = f".v4/risks/repo/{kind}.json"
        ledger.insert(conn, "accepted_risk", claim_id=claim_id, kind="unprovable",
                      who="p@x", why="w" * 50, was_tty=1, git_record=rec,
                      subject_digest="{}", created_at="2026", scope="repo",
                      cover_key="{}")
        if write:
            (root / rec).write_text(json.dumps(
                {"claim": claim_id, "kind": "unprovable", "why": "w" * 50}))
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "sign"], cwd=root, capture_output=True)

    def test_the_older_row_is_history(self):
        from kernel import ledger
        root, conn = self._repo()
        self._sign(root, conn, "old-claim", "surface-proof")
        self._sign(root, conn, "new-claim", "surface-proof")   # replaces the file
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])

    def test_a_row_whose_file_never_existed_is_still_caught(self):
        """The check is not weakened -- only history stops counting as damage."""
        from kernel import ledger
        root, conn = self._repo()
        self._sign(root, conn, "only-claim", "surface-proof", write=False)
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems)
        self.assertIn("only-claim", problems[0])

    def test_the_newest_row_must_still_match_its_file(self):
        from kernel import ledger
        root, conn = self._repo()
        self._sign(root, conn, "old-claim", "surface-proof")
        self._sign(root, conn, "new-claim", "surface-proof")
        (root / ".v4/risks/repo/surface-proof.json").write_text(json.dumps(
            {"claim": "somebody-else", "kind": "unprovable", "why": "w" * 50}))
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "tamper"], cwd=root, capture_output=True)
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems, "an edited record must still be caught")
        self.assertIn("new-claim", " ".join(problems))

    def test_two_kinds_do_not_supersede_each_other(self):
        from kernel import ledger
        root, conn = self._repo()
        self._sign(root, conn, "c-surface", "surface-proof")
        self._sign(root, conn, "c-runtime", "runtime-proof")
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])

    def _on_a_branch(self, root, conn, claim_id, kind, **kw):
        """Sign on a branch and leave it: the row is here, the file is not."""
        import subprocess
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "base"],
                       cwd=root, capture_output=True)
        was = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                             cwd=root, capture_output=True, text=True).stdout.strip()
        subprocess.run(["git", "checkout", "-qb", "other"], cwd=root,
                       capture_output=True)
        self._sign(root, conn, claim_id, kind, **kw)
        subprocess.run(["git", "checkout", "-q", was], cwd=root, capture_output=True)
        return was

    def test_a_record_committed_on_another_branch_is_read_from_git(self):
        """One ledger serves every worktree; a record travels with its branch.

        Two cuts in two worktrees, each signing before either ships: A's row is
        visible from B and A's file is not, so B's audit reads a signature it
        cannot pair -- and `ship` gates on `fatal(chain_problems)`, so neither
        cut leaves until one is merged into the other. Measured 2026-08-27 on
        the wave this repo ran on itself: two of thirteen cuts deadlocked here.

        `fatal` is "the problems that mean somebody changed the record", and a
        file committed on another branch is not that. This function has already
        run `git log --all` to establish the commit, so the bytes are in the
        object store and the same three questions -- does it parse, does it
        agree with the row, is it tracked -- are all answerable there.
        """
        from kernel import ledger
        root, conn = self._repo()
        self._on_a_branch(root, conn, "only-claim", "surface-proof")
        self.assertFalse((root / ".v4/risks/repo/surface-proof.json").is_file())
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])

    def test_and_one_edited_on_that_branch_is_still_caught(self):
        """Read from git, not trusted for being in git.

        The verdict must not get weaker for being somewhere else: a record whose
        content disagrees with its row is the tamper this reconcile exists for,
        and moving where it is read from cannot become a way to hide it.
        """
        import subprocess
        from kernel import ledger
        root, conn = self._repo()
        was = self._on_a_branch(root, conn, "only-claim", "surface-proof")
        subprocess.run(["git", "checkout", "-q", "other"], cwd=root,
                       capture_output=True)
        (root / ".v4/risks/repo/surface-proof.json").write_text(json.dumps(
            {"claim": "somebody-else", "kind": "unprovable", "why": "w" * 50}))
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "tamper"], cwd=root,
                       capture_output=True)
        subprocess.run(["git", "checkout", "-q", was], cwd=root, capture_output=True)
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems, "an edited record must be caught wherever it is")
        self.assertIn("only-claim", " ".join(problems))


class ACoverIsADictOnOneSideAndTheTextOfOneOnTheOther(unittest.TestCase):
    """`v4 ship` printed `chain: BROKEN` for every repo-scoped signature.

    `risk.accept` writes `covers` into the committed record as a dict and
    `cover_key` into the row as `json.dumps(ck, sort_keys=True)`. The
    reconciliation compared them raw, so a dict was never equal to a string and
    the finding named two values a reader can see are identical -- permanently,
    because neither side is wrong and the ledger is append-only.

    It shipped because no test ever put a `covers` key in a record: the loop
    skips a field the record does not carry, so the comparison that was added
    was never run. `doctor` reads the same column and parses it.
    """

    CK = {"kind": "runtime-proof", "checker": "9771abcd"}
    REC = ".v4/risks/repo/runtime-proof.json"

    def _repo(self, *, in_file, in_row):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                    ["git", "config", "user.name", "c"]):
            subprocess.run(cmd, cwd=root, capture_output=True)
        (root / ".v4" / "risks" / "repo").mkdir(parents=True)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="runtime-proof",
                      question="q", subject_refs="[]", checker="runtime-proof",
                      origin="derive", created_at="2026")
        ledger.insert(conn, "accepted_risk", claim_id="c1", kind="unprovable",
                      who="p@x", why="w" * 50, was_tty=1, git_record=self.REC,
                      subject_digest="{}", created_at="2026", scope="repo",
                      cover_key=in_row)
        body = {"claim": "c1", "kind": "unprovable", "why": "w" * 50,
                "scope": "repo", "who": "p@x", "stdin_was_a_tty": True}
        if in_file is not None:
            body["covers"] = in_file
        (root / self.REC).write_text(json.dumps(body, indent=2))
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "sign"], cwd=root, capture_output=True)
        return root, conn

    def test_the_same_cover_written_two_ways_reconciles(self):
        from kernel import ledger
        root, conn = self._repo(in_file=self.CK,
                                in_row=json.dumps(self.CK, sort_keys=True))
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])

    def test_a_cover_that_really_differs_is_still_caught(self):
        """The control. Parsing both sides must not turn the check off."""
        from kernel import ledger
        other = dict(self.CK, checker="ffffffff")
        root, conn = self._repo(in_file=self.CK,
                                in_row=json.dumps(other, sort_keys=True))
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems)
        self.assertIn("covers", problems[0])

    def test_a_record_with_a_cover_and_a_row_without_one_says_so(self):
        """A different fault, and it had the wrong sentence.

        This repo carries one such row. "One of them was edited after the
        other" sends the reader looking for a tamper; the row simply never held
        the value.
        """
        from kernel import ledger
        root, conn = self._repo(in_file=self.CK, in_row="")
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems)
        self.assertIn("carries none", problems[0])
        self.assertNotIn("edited after the other", problems[0])

    def test_neither_side_carrying_one_is_not_a_disagreement(self):
        from kernel import ledger
        root, conn = self._repo(in_file=None, in_row="")
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])


class WhoSignedIsHeldHonestOnBothSides(unittest.TestCase):
    """`signed_by` lived in the committed record and nowhere else for a day.

    The record file is the anchor -- it goes into a commit with a name on it --
    and `reconcile_signatures` is the only thing that makes the file and the
    ledger answer to each other. A value that exists on one side only cannot be
    contradicted, which is the same "wired at one end" state `git_record` was in
    before this function existed.

    The awkward half is history: the column was added on 2026-08-27 to a table
    this repo already held 181 rows in, and the ledger refuses UPDATE, so those
    rows say '' forever. Reporting them would put a permanent problem into the
    verdict `v4 ship` reads -- the failure `superseded` was written to stop.
    """

    REC = ".v4/risks/c1.json"

    def _repo(self, *, in_file, in_row):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                    ["git", "config", "user.name", "c"]):
            subprocess.run(cmd, cwd=root, capture_output=True)
        (root / ".v4" / "risks").mkdir(parents=True)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="lint",
                      question="q", subject_refs="[]", checker="lint",
                      origin="derive", created_at="2026")
        cols = dict(claim_id="c1", kind="unprovable", who="p@x", why="w" * 50,
                    was_tty=0, git_record=self.REC, subject_digest="{}",
                    created_at="2026", scope="task", cover_key="")
        if in_row is not None:
            cols["signed_by"] = in_row
        ledger.insert(conn, "accepted_risk", **cols)
        (root / self.REC).write_text(json.dumps(
            {"claim": "c1", "kind": "unprovable", "why": "w" * 50,
             "scope": "task", "who": "p@x", "stdin_was_a_tty": False,
             "signed_by": in_file}, indent=2))
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "sign"], cwd=root, capture_output=True)
        return root, conn

    def test_the_two_sides_agreeing_is_quiet(self):
        from kernel import ledger
        root, conn = self._repo(in_file=risk.MONITOR, in_row=risk.MONITOR)
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])

    def test_a_record_claiming_a_route_the_row_did_not_is_caught(self):
        """The direction the whole pair exists for: a file that says a second
        session signed this, against a row that says the worker did."""
        from kernel import ledger
        root, conn = self._repo(in_file=risk.MONITOR, in_row=risk.AGENT)
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems)
        self.assertIn("signed_by", problems[0])
        self.assertIn("edited after the other", problems[0])

    def test_a_row_written_before_the_column_existed_is_not_a_finding(self):
        """181 of them here, and none can ever be filled in: this table has an
        UPDATE trigger. Reporting them would be a permanent BROKEN."""
        from kernel import ledger
        root, conn = self._repo(in_file=risk.AGENT, in_row="")
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])

    def test_and_the_exemption_is_not_a_hole_for_other_fields(self):
        """The control. `signed_by` is exempt when the *row* is empty; `who`
        is not, and both go through the same loop."""
        from kernel import ledger
        root, conn = self._repo(in_file=risk.AGENT, in_row="")
        (root / self.REC).write_text(json.dumps(
            {"claim": "c1", "kind": "unprovable", "why": "w" * 50,
             "scope": "task", "who": "somebody-else", "stdin_was_a_tty": False,
             "signed_by": risk.AGENT}, indent=2))
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems)
        self.assertIn("'who'", " ".join(problems))


class OneClaimCanCarryMoreThanOneSignature(unittest.TestCase):
    """The rows were keyed by claim, so all but the last were dropped.

    Signing twice is what the tool asks for -- the message printed at signing
    says the cover lapses the moment the checker changes -- and a claim signed
    task-scoped and then repo-scoped writes two different records.

    Measured on the reference adopter: claim a6016966f1777b96 has four rows,
    three naming `.v4/risks/<claim>.json` and the fourth naming
    `.v4/risks/repo/secret.json`. Keyed by claim, only the fourth survived, so
    the per-claim record was reported as "a signature the ledger never made"
    -- three rows name it -- and the three earlier signatures were checked
    against nothing, which is the direction this function exists for.
    """

    def _repo(self):
        import subprocess, tempfile
        from kernel import ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "a@b"],
                    ["git", "config", "user.name", "c"]):
            subprocess.run(cmd, cwd=root, capture_output=True)
        (root / ".v4" / "risks" / "repo").mkdir(parents=True)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        ledger.insert(conn, "claim", id="c1", task_id="t", kind="secret",
                      question="q", subject_refs="[]", checker="secret",
                      origin="derive", created_at="2026")
        return root, conn

    def _sign(self, root, conn, rec, scope, why):
        import subprocess
        from kernel import ledger
        ledger.insert(conn, "accepted_risk", claim_id="c1", kind="unprovable",
                      who="p@x", why=why, was_tty=1, git_record=rec,
                      subject_digest="{}", created_at="2026", scope=scope,
                      cover_key="")
        (root / rec).write_text(json.dumps(
            {"claim": "c1", "kind": "unprovable", "scope": scope, "why": why}))
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "sign"], cwd=root, capture_output=True)

    TASK_REC = ".v4/risks/c1.json"
    REPO_REC = ".v4/risks/repo/secret.json"

    def test_a_claim_signed_at_two_scopes_reconciles(self):
        from kernel import ledger
        root, conn = self._repo()
        self._sign(root, conn, self.TASK_REC, "task", "t" * 50)
        self._sign(root, conn, self.REPO_REC, "repo", "r" * 50)
        self.assertEqual(ledger.reconcile_signatures(conn, root), [])

    def test_the_earlier_signature_is_still_reconciled(self):
        """The direction that was dead: an earlier row's record, edited.

        Keyed by claim, that row was not in the table at all, so its record
        could say anything.
        """
        from kernel import ledger
        root, conn = self._repo()
        self._sign(root, conn, self.TASK_REC, "task", "t" * 50)
        self._sign(root, conn, self.REPO_REC, "repo", "r" * 50)
        (root / self.TASK_REC).write_text(json.dumps(
            {"claim": "c1", "kind": "unprovable", "scope": "task",
             "why": "edited after the fact"}))
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "tamper"], cwd=root, capture_output=True)
        problems = ledger.reconcile_signatures(conn, root)
        self.assertTrue(problems, "an edited earlier record must be caught")
        self.assertIn("why", " ".join(problems))

    def test_the_record_of_an_earlier_row_is_not_an_orphan(self):
        from kernel import ledger
        root, conn = self._repo()
        self._sign(root, conn, self.TASK_REC, "task", "t" * 50)
        self._sign(root, conn, self.REPO_REC, "repo", "r" * 50)
        said = " ".join(ledger.reconcile_signatures(conn, root))
        self.assertNotIn("the ledger never made", said)


class ARoundThatCreatedNothingIsNotARound(unittest.TestCase):
    """The re-derive budget counted every `v4 ship`, converged or not.

    The loop records a `ship_round` before it can know whether anything came
    back, so three ships that each converged on the first derive spent the
    whole budget and the task became permanently unshippable. Measured on the
    reference adopter: `t-resign` had three rounds, `created=0` on all three,
    and every one of those ships had failed for a reason with nothing to do
    with re-deriving -- the signature chain was broken elsewhere in the repo.

    What the budget is for is printed by the budget itself: "a detector is
    emitting claims that answering it creates". That is divergence. A converged
    round is the opposite.
    """

    def _task(self, rounds):
        import subprocess, tempfile
        from kernel import config, ledger
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        subprocess.run(["git", "init", "-q"], cwd=root, capture_output=True)
        (root / ".v4").mkdir()
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (root / ".v4" / "claim_kinds.json").write_text("{}")
        (root / "detectors").mkdir()
        conn = ledger.connect(root)
        ledger.insert(conn, "task", id="t", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026")
        for n in rounds:
            ledger.insert(conn, "event", task_id="t", claim_id=None,
                          kind="ship_round", actor="kernel",
                          payload=json.dumps({"created": n}), created_at="2026")
        return conn, config.RepoConfig(root)

    def test_three_converged_ships_do_not_exhaust_the_budget(self):
        from kernel import lifecycle
        conn, cfg = self._task([0, 0, 0])
        ok, rep = lifecycle.ship(conn, cfg, "t")
        self.assertNotIn("already spent", rep.get("why", ""))

    def test_three_diverging_rounds_still_do(self):
        """The guard is unchanged for what it was built to stop."""
        from kernel import lifecycle
        conn, cfg = self._task([4, 3, 2])
        ok, rep = lifecycle.ship(conn, cfg, "t")
        self.assertFalse(ok)
        self.assertIn("already spent", rep["why"])

    def test_a_mix_counts_only_the_diverging_ones(self):
        from kernel import lifecycle
        conn, cfg = self._task([0, 5, 0, 0])
        ok, rep = lifecycle.ship(conn, cfg, "t")
        self.assertNotIn("already spent", rep.get("why", ""))

    def test_an_unreadable_payload_is_charged(self):
        """The safe direction: a round nobody can read is a round."""
        from kernel import ledger, lifecycle
        conn, cfg = self._task([])
        for _ in range(3):
            ledger.insert(conn, "event", task_id="t", claim_id=None,
                          kind="ship_round", actor="kernel", payload="not json",
                          created_at="2026")
        ok, rep = lifecycle.ship(conn, cfg, "t")
        self.assertIn("already spent", rep["why"])


class VerifyAndRegisterAskTheSameQuestion(unittest.TestCase):
    """`verify-detector` printed `usable` over a set `register-detector` refused.

    Verify-then-register is the order the documentation gives and the order a
    person reaches for, and the borrowed-fixture rule lived in one of them. So
    the command with no consequences said yes and the one that writes said no,
    about the same detector and the same directory. Reported from an adopter
    that walked into exactly that.
    """

    def test_a_borrowed_set_is_refused_by_verify_too(self):
        ok, report = register.verify_detector(
            repo_root=REPO, detector_path=REPO / "detectors/test_token_shape.py",
            fixtures_dir=REPO / "tests/fixtures/test_token_shape", min_cases=3)
        self.assertFalse(ok)
        self.assertTrue(any("fixture set" in f for f in report["failures"]),
                        report["failures"])

    def test_and_a_shared_analysis_is_accepted_by_both(self):
        """The control: refusing every shared set would pass the test above,
        and `test_expectation`'s two halves call one analysis module."""
        self.assertEqual(register._borrowed_from_a_checker(
            REPO, REPO / "tests/fixtures/test_expectation",
            REPO / "detectors/test_expectation.py"), "")
        ok, _report = register.verify_detector(
            repo_root=REPO, detector_path=REPO / "detectors/test_expectation.py",
            fixtures_dir=REPO / "tests/fixtures/test_expectation", min_cases=3)
        self.assertTrue(ok)


class TheHookLineInstallPrints(unittest.TestCase):
    """`v4 install` asked whether the settings file was there, not what was in it.

    `doctor`'s hook row is called "the file existing is not the hook running"
    and reads the contents. `install` -- the command an adopter runs first, and
    the one whose closing lines say what is still owed -- checked
    `.claude/settings.json.is_file()`. An adopter who already had that file for
    their own reasons was told nothing: hooks on disk, nothing calling them, and
    the write gate off with no line anywhere saying so.
    """

    def _root(self, settings_body=None):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "hooks").mkdir()
        for name in ("write_block.py", "stop_gate.py"):
            (tmp / "hooks" / name).write_text("# hook\n")
        (tmp / ".claude").mkdir()
        if settings_body is not None:
            (tmp / ".claude" / "settings.json").write_text(settings_body)
        return tmp

    def _said(self, root):
        import contextlib
        import io
        from kernel import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli._install_what_is_left(root, [], [], [], 0, [])
        return buf.getvalue()

    def test_no_settings_file_still_says_so(self):
        said = self._said(self._root())
        self.assertIn("2 of 2 hook(s)", said)
        self.assertIn("cp .claude/settings.template.json", said)

    def test_a_settings_file_that_never_names_the_hooks_says_so_too(self):
        """The case that printed nothing: the file is there and the hooks are not."""
        said = self._said(self._root('{"permissions": {"allow": []}}'))
        self.assertIn("2 of 2 hook(s)", said)
        self.assertIn("write_block.py", said)
        self.assertIn("merge", said, "cp would overwrite what they already have")

    def test_one_wired_and_one_not_names_the_one(self):
        said = self._said(self._root('{"hooks": {"PreToolUse": "write_block.py"}}'))
        self.assertIn("1 of 2 hook(s)", said)
        self.assertIn("stop_gate.py", said)
        self.assertNotIn("  write_block.py\n", said)

    def test_both_wired_says_nothing(self):
        """No line is the right output here -- there is nothing left to owe."""
        said = self._said(self._root(
            '{"hooks": {"a": "write_block.py", "b": "stop_gate.py"}}'))
        self.assertNotIn("hook(s)", said)


class OneCliTwoWaysToWriteAList(unittest.TestCase):
    """`--scope` split on commas and `--add` did not, in the same CLI.

    Measured while using it: `v4 scope widen --add 'one.json,two.md'` stored a
    single glob with a comma inside, which matches no path. The command printed
    success and widened nothing, and the writes it was run to allow went on
    being refused. `--scope 'a/**, b/**'` had the mirror of the same defect:
    `split(",")` with no strip stored `' b/**'`, leading space and all, while
    `--forbid` one line above had been stripping all along.

    Both are the same fact -- how this CLI reads a list of globs -- so both are
    fixed here rather than only the one that was noticed.

    And both were only half of it. That pass fixed the *splitting* and left the
    *shape*: `--scope` took one value, so a second `--scope` replaced the first,
    and `--add`/`--drop`/`check --claim` took `nargs="*"`, where a second flag
    does the same. Measured 2026-09-06, both while using this CLI to do other
    work: `v4 task --scope kernel/redgreen.py --scope 'tests/**'` opened a task
    scoped to `tests/**` alone, and `v4 scope widen --add A --add B` widened to
    B. Each printed what it had stored and neither said anything was missing --
    and a scope that is half of what was meant fails nothing, it just makes the
    checkers skip files, which reads exactly like a clean run.

    So every one of them is `action="append"` now, which cannot drop a value,
    and every one arrives at `_list_arg` -- one splitter where there were four.
    """

    def _repo(self):
        import tempfile
        import subprocess
        import json as _json
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for cmd in (["git", "init", "-q"],
                    ["git", "config", "user.email", "t@t"],
                    ["git", "config", "user.name", "t"]):
            subprocess.run(cmd, cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(_json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}))
        (tmp / ".v4" / "claim_kinds.json").write_text("{}")
        (tmp / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
        subprocess.run(["git", "commit", "-qm", "in"], cwd=tmp,
                       capture_output=True)
        return tmp

    REQUEST = ("a request long enough to clear whatever floor this command "
               "applies to the sentence it is given")

    def test_a_comma_list_reaches_widen_as_separate_globs(self):
        from kernel import cli
        self.assertEqual(cli._list_arg(["one.json,docs/SPEC.md"]),
                         ["one.json", "docs/SPEC.md"])

    def test_the_space_separated_form_still_works(self):
        from kernel import cli
        self.assertEqual(cli._list_arg(["a/**", "b/**"]), ["a/**", "b/**"])

    def test_and_the_two_forms_can_be_mixed(self):
        from kernel import cli
        self.assertEqual(cli._list_arg(["a/**, b/**", "c/**"]),
                         ["a/**", "b/**", "c/**"])

    def test_empty_pieces_are_dropped_rather_than_stored(self):
        from kernel import cli
        self.assertEqual(cli._list_arg(["a/**,", "", " , "]), ["a/**"])

    def test_a_repeated_flag_arrives_as_a_list_of_lists(self):
        """`action="append"` with `nargs="*"` is what keeps `--add a b` working
        beside `--add a --add b`, and it hands back nesting. Flattening it here
        is why one splitter can serve both shapes."""
        from kernel import cli
        self.assertEqual(cli._list_arg([["a/**", "b/**"], ["c/**,d/**"]]),
                         ["a/**", "b/**", "c/**", "d/**"])

    def test_a_repeated_scope_keeps_both(self):
        """The measured failure: the second `--scope` replaced the first and
        the task opened able to write half of what was declared."""
        from kernel import cli, ledger, scope as scope_mod
        tmp = self._repo()
        self.assertEqual(
            cli.main(["--repo", str(tmp), "task", "--id", "t-twice",
                      "--request", self.REQUEST,
                      "--scope", "src/**", "--scope", "docs/**"]),
            0)
        conn = ledger.connect_readonly(tmp)
        self.addCleanup(conn.close)
        self.assertEqual(scope_mod.current_scope(conn, "t-twice"),
                         ["src/**", "docs/**"])

    def test_a_repeated_forbid_keeps_both(self):
        from kernel import cli, ledger, scope as scope_mod
        tmp = self._repo()
        self.assertEqual(
            cli.main(["--repo", str(tmp), "task", "--id", "t-forbid",
                      "--request", self.REQUEST, "--scope", "src/**",
                      "--forbid", "a/**", "--forbid", "b/**"]),
            0)
        conn = ledger.connect_readonly(tmp)
        self.addCleanup(conn.close)
        self.assertEqual(sorted(scope_mod.forbidden(conn, "t-forbid")),
                         ["a/**", "b/**"])

    def test_a_repeated_add_keeps_both(self):
        """The other half of the same measurement, on the other flag shape."""
        from kernel import cli, ledger, scope as scope_mod
        tmp = self._repo()
        self.assertEqual(
            cli.main(["--repo", str(tmp), "task", "--id", "t-widen",
                      "--request", self.REQUEST, "--scope", "src/**"]),
            0)
        self.assertEqual(
            cli.main(["--repo", str(tmp), "scope", "widen", "--task", "t-widen",
                      "--add", "a/**", "--add", "b/**",
                      "--why", "both of these belong to this task and the "
                               "point of the case is that both arrive"]),
            0)
        conn = ledger.connect_readonly(tmp)
        self.addCleanup(conn.close)
        self.assertEqual(sorted(scope_mod.current_scope(conn, "t-widen")),
                         ["a/**", "b/**", "src/**"])

    def test_the_space_separated_add_still_works(self):
        """`action="append"` must not cost the form that already worked."""
        from kernel import cli, ledger, scope as scope_mod
        tmp = self._repo()
        cli.main(["--repo", str(tmp), "task", "--id", "t-sp",
                  "--request", self.REQUEST, "--scope", "src/**"])
        self.assertEqual(
            cli.main(["--repo", str(tmp), "scope", "widen", "--task", "t-sp",
                      "--add", "a/**", "b/**",
                      "--why", "the space separated form is the one this CLI "
                               "documented first and it stays"]),
            0)
        conn = ledger.connect_readonly(tmp)
        self.addCleanup(conn.close)
        self.assertEqual(sorted(scope_mod.current_scope(conn, "t-sp")),
                         ["a/**", "b/**", "src/**"])

    def test_a_repeated_claim_selector_reaches_check(self):
        """`check --claim` was the third `nargs="*"`. Its consumer builds a
        `set()`, so nesting it without flattening is a TypeError rather than a
        quiet loss -- this runs the command to say which one happens."""
        from kernel import cli
        tmp = self._repo()
        cli.main(["--repo", str(tmp), "task", "--id", "t-sel",
                  "--request", self.REQUEST, "--scope", "src/**"])
        for argv in (["--claim", "aaaa", "bbbb"],
                     ["--claim", "aaaa", "--claim", "bbbb"]):
            with self.subTest(argv=argv):
                self.assertEqual(
                    cli.main(["--repo", str(tmp), "check", "--task", "t-sel"]
                             + argv), 0)

    def test_task_scope_is_stripped_the_way_forbid_always_was(self):
        """Read back from the ledger, not from the line the command printed."""
        from kernel import cli, ledger, scope as scope_mod
        tmp = self._repo()
        self.assertEqual(
            cli.main(["--repo", str(tmp), "task", "--id", "t-space",
                      "--request", self.REQUEST, "--scope", "src/**, docs/**"]),
            0)
        conn = ledger.connect_readonly(tmp)
        self.addCleanup(conn.close)
        self.assertEqual(scope_mod.current_scope(conn, "t-space"),
                         ["src/**", "docs/**"],
                         "' docs/**' with the leading space matches no file")

    def test_a_scope_of_only_separators_is_refused(self):
        """Otherwise the task opens able to write nothing and says nothing."""
        from kernel import cli
        tmp = self._repo()
        self.assertEqual(
            cli.main(["--repo", str(tmp), "task", "--id", "t-empty",
                      "--request", self.REQUEST, "--scope", " , "]),
            2)


class ADetectorRaisingAKindNobodyRegisters(unittest.TestCase):
    """`doctor` asked about detectors and could not see the ones that go stale.

    Its detector row filters `if not p.name.startswith(("_", "always_"))`,
    because it is asking whether a *conditional* detector passed its fixture
    gate -- a question `always_*` is exempt from. So the file class a kind trim
    strands was the one class that row could never mention. Measured on the
    reference adopter: five stranded `always_*` detectors, on every task since
    the trim, while `doctor` reported `9 conditional detector(s) match`.
    """

    LIVE = 'print("V4-CLAIM: kind=lint")\n'
    STRANDED = 'print("V4-CLAIM: kind=sweep-current")\n'
    #: The spelling the old extractor could not read: the name is a constant,
    #: so `kind=` never appears beside it.
    VIA_CONSTANT = 'KIND = "runtime-proof"\nprint(f"V4-CLAIM: kind={KIND}")\n'

    def _repo(self, detectors, kinds):
        import tempfile
        d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        (d / ".v4").mkdir()
        (d / ".v4" / "claim_kinds.json").write_text(json.dumps(kinds))
        (d / "detectors").mkdir()
        for name, body in detectors.items():
            (d / "detectors" / name).write_text(body)
        return d

    def _row(self, root):
        from kernel import doctor
        out = []
        doctor._check_every_detector_raises_a_registered_kind(root, out)
        self.assertEqual(len(out), 1, "the group emits exactly one row")
        return out[0]

    def test_a_stranded_always_detector_is_named(self):
        row = self._row(self._repo(
            {"always_lint.py": self.LIVE, "always_sweep.py": self.STRANDED},
            {"lint": {}}))
        self.assertEqual(row["status"], "bad")
        self.assertIn("always_sweep.py", row["detail"])
        self.assertIn("sweep-current", row["detail"])
        self.assertNotIn("always_lint.py", row["detail"])

    def test_a_repo_whose_detectors_all_land_is_ok(self):
        row = self._row(self._repo({"always_lint.py": self.LIVE}, {"lint": {}}))
        self.assertEqual(row["status"], "ok")

    def test_a_kind_held_in_a_constant_is_read(self):
        """`kind=([a-z-]+)` returned the empty set for every detector that names
        its kind once and interpolates it -- `runtime_proof.py` and
        `surface_proof.py` in this repo. Empty is not "raises nothing", it is
        "this reader cannot tell", and `copy_files` holds a detector back on
        `raised and raised <= held_kinds`, where an empty `raised` is falsey.
        """
        row = self._row(self._repo(
            {"runtime_proof.py": self.VIA_CONSTANT}, {"lint": {}}))
        self.assertEqual(row["status"], "bad")
        self.assertIn("runtime-proof", row["detail"])
