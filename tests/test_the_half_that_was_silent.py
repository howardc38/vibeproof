"""Five mechanisms with a half that never reported anything.

    python3 -m unittest tests.test_the_half_that_was_silent -v

Each was reproduced before it was touched, and each is the same shape: one side
of a pair was wired and the other was not, so the difference could only be seen
by reading both.

  * `_is_open` existed twice with one docstring -- "a hook that cannot tell must
    not start ignoring what it was told" -- and the two copies disagreed about
    exactly that case: `write_block` opened the database outside its `try` and
    raised where `stop_gate` answered
  * both hooks' `ImportError` fallbacks omitted `record_seen`, which each calls
    on the path the fallback exists for, so an import failure became an
    `AttributeError` inside the handler reporting it
  * `accept` handed the whole parent environment to the repo's own test command
    while `runner.child_env` -- the declared owner -- allowlists, so one suite
    ran in two environments depending on which command reached it
  * three timeouts for one suite: 1800 in `_run`, 3600 in the shell branch,
    and `config.DEFAULT_TEST_TIMEOUT`, which the constant's own comment calls
    "One number, because there were two"
  * `run_detector` parses a detector's `--out` and says "Returned rather than
    stored, so the caller that owns the ledger decides"; the caller bound it as
    `det_out` and decided nothing -- one occurrence in the repo, the binding
"""

from __future__ import annotations


def ledger_mod_for_derive():
    from kernel import ledger
    return ledger

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "hooks"))

import _framework                                               # noqa: E402
from kernel import accept as accept_mod                         # noqa: E402
from kernel import config as config_mod                         # noqa: E402
from kernel import runner                                       # noqa: E402


class OneRuleAboutAnUnreadableLedger(unittest.TestCase):
    """Two hooks stated it and one of them raised instead."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                  ["git", "config", "user.name", "t"]):
            subprocess.run(a, cwd=self.tmp, capture_output=True)

    def test_a_database_that_will_not_open_answers_rather_than_raises(self):
        """`sqlite3.connect(mode=ro)` raises for a file that is not a database,
        and that is the case the docstring is about."""
        gitdir = self.tmp / ".git" / "v4"
        gitdir.mkdir(parents=True, exist_ok=True)
        (gitdir / "ledger.db").write_text("this is not a database")
        self.assertIs(_framework.is_open(self.tmp, "t-anything"), True)

    def test_both_hooks_answer_the_same_way_through_their_own_names(self):
        """Called by the names the findings are filed under, not only through
        the owner they share. `redgreen` traces the symbol a claim names, so a
        test that only exercises the delegate never enters the thing that was
        repaired -- measured, and it is why this case exists."""
        import importlib.util

        gitdir = self.tmp / ".git" / "v4"
        gitdir.mkdir(parents=True, exist_ok=True)
        (gitdir / "ledger.db").write_text("this is not a database")
        answers = []
        for name in ("write_block", "stop_gate"):
            spec = importlib.util.spec_from_file_location(
                f"_hook_{name}", ROOT / "hooks" / f"{name}.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            answers.append(mod._is_open(self.tmp, "t-anything"))
        self.assertEqual(answers, [True, True],
                         "the two hooks disagree about an unreadable ledger")

    def test_a_repo_with_no_ledger_at_all_answers_true(self):
        self.assertIs(_framework.is_open(self.tmp, "t-anything"), True)



class AFallbackThatCanDoWhatItIsFor(unittest.TestCase):
    """The `ImportError` stand-in omitted the one function the path that
    reaches it calls."""

    def _run(self, hook, payload):
        return subprocess.run([sys.executable, str(ROOT / "hooks" / f"{hook}.py")],
                              input=payload, capture_output=True, cwd=ROOT)


    def test_the_mark_names_a_row_even_with_no_command(self):
        """Enters `bash_guard._mark` by name. The unreadable-payload paths hand
        it an empty command, and it names the row after `cmd.split()[0]`."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_hook_bg", ROOT / "hooks" / "bash_guard.py")
        bg = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bg)
        seen = {}
        real = bg._framework.record_seen
        bg._framework.record_seen = lambda root, tid, head, **kw: seen.setdefault(
            "head", head) or ""
        self.addCleanup(setattr, bg._framework, "record_seen", real)
        bg._mark(ROOT, "", allowed=True, basis="unreadable payload")
        self.assertEqual(seen.get("head"), "(no command)")

    def test_and_the_hooks_still_answer(self):
        for hook in ("bash_guard", "write_block"):
            r = self._run(hook, b"{}")
            self.assertEqual(r.returncode, 0, hook)
            self.assertEqual(r.stdout.decode().strip(), "{}", hook)


class OneEnvironmentForOneSuite(unittest.TestCase):
    """`accept` handed the whole parent environment to a command the repo
    declares, bypassing the module that owns the question."""

    def test_the_owner_withholds_what_the_parent_happens_to_hold(self):
        env = runner.child_env({"SECRET_TOKEN": "x", "PATH": "/usr/bin",
                                "HOME": "/home/x"})
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertIn("PATH", env)

    def test_and_accept_asks_it(self):
        """Run it. `accept.run` builds the child environment before it does
        anything else, so a repo whose `test_command` prints the variable it
        was given answers the question -- and the answer is what a checker
        spawned by `v4 check` would have got."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                  ["git", "config", "user.name", "t"]):
            subprocess.run(a, cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        probe = tmp / "probe.py"
        probe.write_text("import os, sys\n"
                         "sys.exit(0 if 'ACCEPT_ENV_PROBE' not in os.environ "
                         "else 1)\n")
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": [sys.executable, str(probe)],
             "policy": "allow_accepted_risk"}), encoding="utf-8")

        os.environ["ACCEPT_ENV_PROBE"] = "must not reach the child"
        self.addCleanup(os.environ.pop, "ACCEPT_ENV_PROBE", None)
        rows = accept_mod.run(tmp, tests=True, fixtures=False, docs=False)
        step, ok, _detail = rows[0]
        self.assertEqual(step, "tests")
        self.assertTrue(ok, "the parent's own variable reached the child")

    def test_the_two_paths_it_adds_are_still_there(self):
        """Withholding is the repair; withholding the repo's own tree from a
        checker running against it would be a different defect."""
        env = runner.child_env()
        self.assertIn("PYTHONPATH", env)


class OneNumberForOneWall(unittest.TestCase):
    def test_the_suite_timeout_comes_from_the_constant(self):
        cfg = config_mod.RepoConfig(ROOT)
        self.assertEqual(accept_mod._suite_timeout(cfg),
                         config_mod.DEFAULT_TEST_TIMEOUT)

    def test_and_a_repo_that_declares_one_gets_it(self):
        class Fake:
            config = {"test_timeout_sec": 120}
        self.assertEqual(accept_mod._suite_timeout(Fake()), 120)

    def test_a_repo_that_wrote_the_sentinel_has_not_declared_one(self):
        """`declared` treats `UNANSWERED` as "has not said", which is why the
        constant's comment says a bare `.get` produced a `ValueError`."""
        class Fake:
            config = {"test_timeout_sec": config_mod.UNANSWERED}
        self.assertEqual(accept_mod._suite_timeout(Fake()),
                         config_mod.DEFAULT_TEST_TIMEOUT)



class WhatADetectorWroteReachesTheLedger(unittest.TestCase):
    def _derive_with_output(self, write_output=True):
        """Run a real detector in a fresh repo, without the maintainer's ledger."""
        from kernel import derive as derive_mod
        from kernel.config import RepoConfig

        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for a in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                  ["git", "config", "user.name", "t"]):
            subprocess.run(a, cwd=tmp, capture_output=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": "true", "policy": "allow_accepted_risk"}),
            encoding="utf-8")
        (tmp / ".v4" / "claim_kinds.json").write_text("{}", encoding="utf-8")
        (tmp / ".v4" / "detectors.json").write_text("{}", encoding="utf-8")
        (tmp / "detectors").mkdir()
        program = ("import argparse, json\nfrom pathlib import Path\n"
                   "p=argparse.ArgumentParser()\n"
                   "p.add_argument('--subject');p.add_argument('--facts');p.add_argument('--out')\n"
                   "a=p.parse_args()\n")
        if write_output:
            program += ("Path(a.out).write_text(json.dumps({'proof':'from-process',"
                        "'password':'hunter2'}))\n")
        (tmp / "detectors" / "always_payload.py").write_text(program)
        (tmp / "app.py").write_text("value = 1\n")
        subprocess.run(["git", "add", "."], cwd=tmp, check=True, capture_output=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-qm", "fixture"],
                       cwd=tmp, check=True, capture_output=True)
        conn = ledger_mod_for_derive().connect(tmp)
        self.addCleanup(conn.close)
        with ledger_mod_for_derive().writing(conn):
            ledger_mod_for_derive().insert(
                conn, "task", id="t", request="r", scope_globs=["**"],
                base_commit="", created_at="2026-09-05T00:00:00+00:00")
        res = derive_mod.derive(conn, RepoConfig(tmp), task_id="t",
                                scope_globs=["**"], subject_files=["app.py"],
                                phase="check")
        rows = conn.execute("SELECT payload FROM event WHERE kind = 'detector_run'").fetchall()
        self.assertEqual(len(rows), 1)
        return res, json.loads(rows[0]["payload"])

    def test_the_row_carries_the_out_payload(self):
        _, payload = self._derive_with_output()
        self.assertEqual(payload["out"], {"proof": "from-process", "password": "[redacted]"})
        self.assertTrue(payload["ran"])

    def test_a_live_derive_records_what_a_detector_wrote(self):
        res, payload = self._derive_with_output(write_output=False)
        self.assertTrue(res["detectors_ran"])
        self.assertIsNone(payload.get("out"))

    def test_and_it_goes_through_the_redactor(self):
        from kernel import derive
        self.assertEqual(derive._redact_json({"password": "hunter2"}, ROOT),
                         {"password": "[redacted]"})
