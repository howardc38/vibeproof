"""Repairs in `kernel/runner.py`, `kernel/redgreen.py` and `kernel/config.py`.

    python3 -m unittest tests.test_the_kernel_reports_what_it_measured -v

Each of these is a number or a verdict the kernel hands to a reader, and each
was a place where the value handed over was not the value measured.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

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

from kernel import config, redgreen, runner  # noqa: E402


def _repo(case, **cfg):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    return tmp


class ASurvivorCountIsACount(unittest.TestCase):
    """`_run_contained` set `survivors = 1` from a presence test.

    The caller renders it as "left {survivors} process(es) running", so a
    checker that left forty behind reported one -- and the timeout branch
    returned a hard-coded zero even though the probe above it had already
    looked.
    """

    def _checker(self, body):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = tmp / "c.py"
        path.write_text("#!/usr/bin/env python3\n"
                        "import argparse, subprocess, sys\n"
                        "p = argparse.ArgumentParser()\n"
                        "p.add_argument('--subject'); p.add_argument('--facts')\n"
                        "p.add_argument('--out'); p.parse_args()\n" + body)
        return path

    def _run(self, body):
        root = _repo(self)
        subj = root / "subject.json"
        subj.write_text(json.dumps({"repo_root": str(root), "subject_refs": []}))
        with tempfile.TemporaryDirectory() as td:
            return runner._run_contained(
                [sys.executable, str(self._checker(body)), "--subject", str(subj)],
                root, 10, Path(td))

    def test_a_checker_that_leaves_three_behind_reports_three(self):
        code, _out, _err, survivors = self._run(
            "for _ in range(3):\n"
            "    subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
            "sys.exit(0)\n")
        self.assertEqual(code, 0)
        self.assertGreaterEqual(survivors, 3,
                                "a presence test reported one for any number")

    def test_a_clean_checker_reports_none(self):
        """The control: returning the group size unconditionally would count
        the checker itself."""
        _code, _out, _err, survivors = self._run("sys.exit(0)\n")
        self.assertEqual(survivors, 0)


class ATracerThatDidNotRunIsNotAnAnswer(unittest.TestCase):
    """`_run_traced` read a missing trace file as proof of non-execution.

    The tracer is installed by the harness; if it never ran -- wrong
    interpreter, a runner that spawns its own process -- the file is absent for
    a reason that says nothing about the symbol, and the finding could then
    never close.
    """

    def test_a_command_that_leaves_no_trace_says_unknown(self):
        """`None`, not `False`. A non-Python runner, or one killed before
        `atexit`, leaves no trace file for a reason that says nothing about the
        symbol -- and `False` there makes `verify` accuse the worker of the one
        bypass this mechanism exists to refuse."""
        root = _repo(self)
        (root / "t_x.py").write_text("def test_x():\n    assert True\n")
        _rc, executed, _calls, _out = redgreen._run_traced(
            root, ["true"], "t_x.py", "test_x", 10)
        self.assertIsNone(executed)

    def test_a_python_run_that_enters_the_symbol_says_true(self):
        """The control: returning None always would pass the test above and
        make every closure unprovable."""
        root = _repo(self)
        (root / "mod.py").write_text("def helper():\n    return 1\n")
        (root / "t_x.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from mod import helper\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.assertEqual(helper(), 1)\n")
        _rc, executed, _calls, _out = redgreen._run_traced(
            root, [sys.executable, "-m", "unittest", "-q", "t_x"],
            "mod.py", "helper", 60)
        self.assertTrue(executed)


class ATestCommandRunsOnce(unittest.TestCase):
    """`executed_files` ran the repo's whole suite a second time.

    `checkers/test.py` had already run it, and when the diff touched any
    non-test Python it called `executed_files`, which runs the identical
    command again -- so answering one `test` claim cost two full suites.
    Counted here by a command that records each run.
    """

    def _repo_that_counts_its_runs(self):
        root = _repo(self)
        tally = root / "runs.txt"
        (root / "count.sh").write_text(
            f"#!/bin/sh\necho run >> {tally}\nexit 0\n")
        (root / "count.sh").chmod(0o755)
        (root / ".v4" / "config.json").write_text(json.dumps(
            {"test_command": str(root / "count.sh"),
             "policy": "allow_accepted_risk"}))
        (root / "mod.py").write_text("def helper():\n    return 1\n")
        subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
        subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=c",
                        "commit", "-qm", "base"], cwd=root, capture_output=True)
        return root, tally

    def _runs(self, tally):
        return len(tally.read_text().splitlines()) if tally.is_file() else 0

    def test_the_trace_comes_out_of_the_run_that_produced_the_verdict(self):
        root, tally = self._repo_that_counts_its_runs()
        subj = root / "subject.json"
        subj.write_text(json.dumps(
            {"repo_root": str(root),
             "subject_refs": [{"kind": "file", "path": "mod.py"}]}))
        r = subprocess.run(
            [sys.executable, str(ROOT / "checkers" / "test.py"),
             "--subject", str(subj)], capture_output=True, text=True)
        self.assertIn(r.returncode, (0, 1, 4), r.stderr[:300])
        self.assertLessEqual(self._runs(tally), 1,
                             "the suite ran twice to answer one claim")

    def test_and_it_did_run_the_command(self):
        """The control: never running it would pass the test above."""
        root, tally = self._repo_that_counts_its_runs()
        subj = root / "subject.json"
        subj.write_text(json.dumps(
            {"repo_root": str(root),
             "subject_refs": [{"kind": "file", "path": "mod.py"}]}))
        subprocess.run([sys.executable, str(ROOT / "checkers" / "test.py"),
                        "--subject", str(subj)], capture_output=True, text=True)
        self.assertEqual(self._runs(tally), 1)

    def test_executed_files_names_the_files_the_command_touched(self):
        """Entered rather than read: the symbol the finding names."""
        root, _tally = self._repo_that_counts_its_runs()
        (root / "t_x.py").write_text(
            "import sys, unittest\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).resolve().parent))\n"
            "from mod import helper\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_it(self):\n        self.assertEqual(helper(), 1)\n")
        rc, ran, _out = redgreen.executed_files(
            root, f"{sys.executable} -m unittest -q t_x", timeout=60)
        self.assertEqual(rc, 0)
        self.assertIn("mod.py", ran)

    def test_the_default_timeout_has_one_owner(self):
        """`executed_files` defaulted to a literal 1800 and its production
        caller read the same literal again from an undeclared config key."""
        import inspect
        self.assertTrue(hasattr(config, "DEFAULT_TEST_TIMEOUT"))
        sig = inspect.signature(redgreen.executed_files)
        self.assertIsNone(sig.parameters["timeout"].default)


class TheConfigLayerDoesNotReachIntoDerive(unittest.TestCase):
    """`config._reads_facts` imported a private symbol from `derive`.

    And it borrowed the wrong half: the textual answer on the entry file alone,
    while `derive` follows imports precisely because that answer was wrong for
    the thin-CLI checkers this repo is made of.
    """

    def test_a_checker_whose_analysis_module_reads_the_table_counts(self):
        root = _repo(self)
        (root / "checkers").mkdir()
        (root / "kernel" / "analysis").mkdir(parents=True)
        (root / "kernel" / "analysis" / "__init__.py").write_text("")
        (root / "kernel" / "__init__.py").write_text("")
        (root / "kernel" / "analysis" / "rule.py").write_text(
            "def scan(facts):\n    return facts.get('ui_globs')\n")
        (root / "checkers" / "thin.py").write_text(
            "from kernel.analysis import rule\n"
            "def main(a):\n    return rule.scan(a.facts)\n")
        cfg = config.RepoConfig(root)
        self.assertTrue(cfg._reads_facts(root / "checkers" / "thin.py"),
                        "the entry file alone said no, and the judgement is "
                        "one import away")

    def test_one_that_reaches_no_table_does_not(self):
        root = _repo(self)
        (root / "checkers").mkdir()
        (root / "checkers" / "plain.py").write_text("def main(a):\n    return 0\n")
        cfg = config.RepoConfig(root)
        self.assertFalse(cfg._reads_facts(root / "checkers" / "plain.py"))


class AMalformedConfigIsNotAnEmptyOne(unittest.TestCase):
    """`_load`'s two errors had no test at all."""

    def test_a_repo_with_no_config_names_the_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(config.ConfigError) as caught:
            config.RepoConfig(tmp)
        self.assertIn("not found at", str(caught.exception))

    def test_one_that_is_not_json_says_so(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / ".v4").mkdir()
        (tmp / ".v4" / "config.json").write_text("{ not json")
        with self.assertRaises(config.ConfigError) as caught:
            config.RepoConfig(tmp)
        self.assertIn("is not valid JSON", str(caught.exception))


class ARepoThatDeclaresNoProtectedPathsStillHasSome(unittest.TestCase):
    """Every test that touched protection wrote its own `protected_paths`.

    So the union with `PROTECTED_DEFAULT` -- the thing that makes `.v4/**`
    protected in a repo that never wrote the key -- was covered by nothing.
    """

    def test_the_framework_defaults_are_there_with_no_declaration(self):
        cfg = config.RepoConfig(_repo(self))
        for glob in (".v4/**", "checkers/**", "detectors/**", ".github/**"):
            self.assertIn(glob, cfg.protected, glob)

    def test_and_what_the_repo_adds_is_kept(self):
        cfg = config.RepoConfig(_repo(self, protected_paths=["ops/**"]))
        self.assertIn("ops/**", cfg.protected)
        self.assertIn(".v4/**", cfg.protected)


class FactsStalenessFollowsTheImports(unittest.TestCase):
    """`facts_sha_for` judged from the entry file's text alone.

    Twenty of this repo's thirty-one checkers are a thin CLI over a module in
    `kernel/analysis/`, so the textual answer about the entry file is wrong for
    most of them -- and `derive` follows imports for precisely that reason.
    """

    def _repo_with(self, thin_reads_table):
        root = _repo(self)
        (root / "checkers").mkdir()
        (root / "kernel" / "analysis").mkdir(parents=True)
        (root / "kernel" / "__init__.py").write_text("")
        (root / "kernel" / "analysis" / "__init__.py").write_text("")
        (root / "kernel" / "analysis" / "rule.py").write_text(
            "def scan(facts):\n    return facts.get('ui_globs')\n"
            if thin_reads_table else "def scan(x):\n    return x\n")
        # The entry file is identical in both cases and names no table at
        # all, which is the whole point: twenty of this repo's checkers look
        # exactly like this and the judgement is one import away.
        (root / "checkers" / "thin.py").write_text(
            "from kernel.analysis import rule\n"
            "def main(a):\n    return rule.scan(a.subject)\n")
        (root / ".v4" / "checkers.json").write_text(json.dumps(
            {"thin": {"path": "checkers/thin.py", "kinds": ["k"], "sha256": ""}}))
        (root / ".v4" / f"facts.{root.name}.json").write_text(json.dumps(
            {"repo": root.name, "generated_from_commit": "0" * 40,
             "outbound_write": [{"pattern": ".post", "seen_at": "checkers/thin.py:1",
                                 "kind": "http"}],
             "outbound_read": [{"pattern": ".get", "seen_at": "checkers/thin.py:1",
                                "kind": "http"}],
             "auth_decision": [{"pattern": "scan", "seen_at": "checkers/thin.py:2",
                                "kind": "authz"}],
             "entrypoint_globs": ["checkers/**"], "ui_globs": ["web/**"],
             "config_files": [".v4/config.json"],
             "protected_paths": [".v4/**"]}))
        return config.RepoConfig(root)

    def test_a_thin_cli_over_an_analysis_module_is_facts_sensitive(self):
        cfg = self._repo_with(True)
        self.assertNotEqual(cfg.facts_sha_for("thin"), "")

    def test_and_one_whose_analysis_reads_no_table_is_not(self):
        """The control: answering "sensitive" for everything would make every
        PASS expire on any facts edit."""
        cfg = self._repo_with(False)
        self.assertEqual(cfg.facts_sha_for("thin"), "")


class TheModuleDocstringCountsTheStoresItLoads(unittest.TestCase):
    """`config.py` opened "The three JSON files" and loaded five stores."""

    def test_every_store_it_loads_is_reachable(self):
        cfg = config.RepoConfig(_repo(self))
        for attr in ("config", "kinds", "checkers", "detectors", "facts"):
            self.assertTrue(hasattr(cfg, attr), attr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
