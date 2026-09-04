"""Seven swallowed exceptions, and the sentence each of them owed.

    python3 -m unittest tests.test_a_check_that_did_not_run_says_so -v

One fact, in seven places: an `except` that returns the same value a clean
answer returns, so "this check could not run" and "this check ran and found
nothing" leave identical output. Every one of these is on a path where the
answer is durable -- a ship report, an append-only export, a claim that will
or will not exist, a guard standing down -- and on every one of them the
recovery was already right. What was missing was the sentence.

Each case here asserts the sentence and not the recovery, because the recovery
is what the swallow got right: the hook still allows, the floor still filters,
the detector still scans the rest. All seven fail against 0785a00.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, config, coverage, doctrine  # noqa: E402
from kernel.analysis import redaction  # noqa: E402


def _load(case, name: str, path: Path):
    """A checker or hook loaded from its own file, the way the kernel runs it."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _bare_repo(case, **cfg) -> Path:
    """A git repo with a `.v4/` and nothing else opinionated in it."""
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q", "."], cwd=tmp, check=True)
    (tmp / ".v4").mkdir()
    body = {"test_command": "true", "policy": "allow_accepted_risk"}
    body.update(cfg)
    (tmp / ".v4" / "config.json").write_text(json.dumps(body), encoding="utf-8")
    (tmp / ".v4" / "claim_kinds.json").write_text("{}", encoding="utf-8")
    (tmp / ".v4" / "checkers.json").write_text("{}", encoding="utf-8")
    return tmp


class TheShipReportSaysWhenItCouldNotAsk(unittest.TestCase):
    """`kernel/cli.py::_print_uncovered`.

    The function exists to stop `SHIP` plus a green suite reading as "this
    works" -- its docstring says so -- and `except Exception: return` deleted
    the line from the report, leaving a repo whose risk rubric will not parse
    printing exactly like a repo where every operational-risk class has a kind.
    """

    def _uncovered(self, root) -> str:
        cfg = config.RepoConfig(root)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli._print_uncovered(cfg)
        return buf.getvalue()

    def test_a_rubric_that_will_not_parse_is_reported_not_dropped(self):
        root = _bare_repo(self)
        (root / ".v4" / "risk_rubric.json").write_text("{not json",
                                                       encoding="utf-8")
        out = self._uncovered(root)
        self.assertIn("risk_rubric.json", out,
                      "a rubric that could not be read left no line in the "
                      "ship report at all")
        self.assertIn("could not be read", out)
        self.assertNotIn("have no kind here, so this ship says nothing about "
                         "them", out,
                         "it must not report a count it never computed")

    def test_no_rubric_at_all_stays_silent(self):
        """The other half. A repo that never declared a rubric owes no line,
        and turning the repair into "print something every time" would make the
        new sentence furniture."""
        self.assertEqual("", self._uncovered(_bare_repo(self)))

    def test_a_rubric_that_reads_still_answers_the_question(self):
        """And the repair did not cost the answer it was protecting."""
        root = _bare_repo(self)
        (root / ".v4" / "risk_rubric.json").write_text(json.dumps({
            "source": "a test",
            "rows": [{"n": 1, "category": "backups exist and restore",
                      "answered_by": ["nothing-registered-here"],
                      "coverage": "none", "fill": "judgment"}],
        }), encoding="utf-8")
        out = self._uncovered(root)
        self.assertIn("1 of 1 operational-risk class(es) have no kind", out)

    def test_the_owner_of_the_question_tells_the_two_apart(self):
        """Where the distinction lives. Both readers of this file are one hop
        from a person forming an impression, and putting an `is_file()` test in
        each of them is two derivations of one boundary."""
        self.assertIsNone(coverage.rubric(_bare_repo(self)))
        broken = _bare_repo(self)
        (broken / ".v4" / "risk_rubric.json").write_text("{not json",
                                                         encoding="utf-8")
        with self.assertRaises(coverage.RubricUnreadable):
            coverage.rubric(broken)

    def test_the_coverage_command_stops_claiming_the_file_is_absent(self):
        """The other surface. `v4 coverage` printed "no .v4/risk_rubric.json
        here" about a file that is here."""
        root = _bare_repo(self)
        (root / ".v4" / "risk_rubric.json").write_text("{not json",
                                                       encoding="utf-8")
        args = argparse.Namespace(repo=str(root), acceptance=None,
                                  predecessor=False)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), mock.patch.object(sys, "stderr", err):
            code = cli.cmd_coverage(args)
        self.assertEqual(4, code)
        self.assertNotIn("no .v4/risk_rubric.json here", out.getvalue())
        self.assertIn("does not read", err.getvalue())


class TheWriteHookSaysWhenItCannotFindTheTask(unittest.TestCase):
    """`hooks/write_block.py::open_task`.

    `except sqlite3.Error: return None` gave the same answer as "the ledger was
    read and nothing is open" -- and that branch writes no `hook_seen` and
    printed nothing, while `current_scope` in the same file has printed the
    exception and marked `basis=unreadable` for the identical class of failure
    since it was written.
    """

    def setUp(self):
        self.wb = _load(self, "wb_probe", ROOT / "hooks" / "write_block.py")

    def _repo_with_a_broken_ledger(self) -> Path:
        root = _bare_repo(self)
        db = root / ".git" / "v4" / "ledger.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_text("this is not a sqlite database\n", encoding="utf-8")
        return root

    def test_an_unreadable_ledger_is_not_no_task_open(self):
        root = self._repo_with_a_broken_ledger()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            answer = self.wb.open_task(root)
        self.assertIs(answer, self.wb.UNREADABLE,
                      "a ledger that raised came back as None, which is the "
                      "ledger's answer that no task is open")
        self.assertIn("could not be read", err.getvalue())
        self.assertIn("not checking scope", err.getvalue())

    def test_the_error_the_ledger_gave_is_in_the_sentence(self):
        """Not a generic 'something went wrong': the class and the message, so
        `file is not a database` and a permissions error read differently."""
        root = self._repo_with_a_broken_ledger()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.wb.open_task(root)
        self.assertIn("DatabaseError", err.getvalue())

    def test_a_ledger_that_reads_and_holds_nothing_is_still_none(self):
        """The state the sentinel had to be told apart from. A real, readable
        ledger with no open task keeps answering `None`, silently -- there is
        nothing to enforce and saying so on every write is noise."""
        root = _bare_repo(self)
        from kernel import ledger as ledger_mod
        ledger_mod.connect(root).close()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.assertIsNone(self.wb.open_task(root))
        self.assertEqual("", err.getvalue())


class TheRedactionFloorSaysWhenItRanNarrow(unittest.TestCase):
    """`kernel/analysis/redaction.py::redact`.

    `except Exception: pass` around `table_for` reverted the last filter before
    an append-only, committed export to the three shapes in `_REDACTIONS` --
    which is `table_for`'s own docstring being disobeyed one layer down: "a
    table that does not parse is an error, never an empty one".
    """

    #: Says what it is, in the value, so a copy of this line stays true.
    FAKE = "AKIA" + "TESTNOTAREALKEY0"

    def _repo_with_a_broken_pattern_table(self) -> Path:
        root = _bare_repo(self)
        (root / ".v4" / "secret_patterns.json").write_text("{not json",
                                                           encoding="utf-8")
        return root

    def test_the_shipped_families_still_apply(self):
        """`AKIA` is in the shipped table and in neither `_REDACTIONS` nor a
        repo table that will not parse, so before this it went into the ledger
        in the clear."""
        root = self._repo_with_a_broken_pattern_table()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            out = redaction.redact(f"key={self.FAKE}", root)
        self.assertNotIn(self.FAKE, out)
        self.assertIn("[redacted]", out)

    def test_the_narrow_pass_is_said_out_loud(self):
        root = self._repo_with_a_broken_pattern_table()
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            redaction.redact(f"key={self.FAKE}", root)
        said = err.getvalue()
        self.assertIn("secret_patterns.json", said)
        self.assertIn("JSONDecodeError", said)
        self.assertIn("none of the ones this repo added", said)

    def test_a_table_that_reads_is_silent_and_wider(self):
        """The repo's own families are applied and nothing is printed: this
        must not become a line on every checker run."""
        root = _bare_repo(self)
        (root / ".v4" / "secret_patterns.json").write_text(json.dumps({
            "patterns": [{"id": "house-token", "label": "this repo's own token",
                          "kind": "token", "order": 90,
                          "regex": r"hs_[A-Za-z0-9]{10,}"}],
        }), encoding="utf-8")
        err = io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            out = redaction.redact("t=hs_notarealtoken0", root)
        self.assertEqual("", err.getvalue())
        self.assertNotIn("hs_notarealtoken0", out)


class TheHookFrameworkSaysWhichImportBroke(unittest.TestCase):
    """`hooks/_framework.py::_module`.

    The handler dropped the import exception on the argument that `why` is what
    a caller prints. `why` runs `on_path`, and `on_path` returns `None` --
    success -- in exactly this case: the path resolved, the module did not
    import. So `why` answered `""`, and every caller writes `why(...) or
    '<something else>'`.

    A subprocess, because the point is which `kernel` gets imported and this
    test process has already imported the real one.
    """

    PROBE = """
import sys
sys.path.insert(0, {hooks!r})
import _framework
root = {root!r}
mod = _framework.ledger(root)
print("MODULE:", mod)
print("WHY:", _framework.why(root))
"""

    def test_a_kernel_that_will_not_import_reaches_the_caller(self):
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        (home / "kernel").mkdir()
        (home / "kernel" / "__init__.py").write_text("", encoding="utf-8")
        (home / "kernel" / "ledger.py").write_text(
            "raise RuntimeError('half a checkout')\n", encoding="utf-8")

        script = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, script, ignore_errors=True)
        probe = script / "probe.py"
        probe.write_text(
            self.PROBE.format(hooks=str(ROOT / "hooks"), root=str(home)),
            encoding="utf-8")

        env = dict(os.environ, V4_HOME=str(home))
        env.pop("PYTHONPATH", None)
        r = subprocess.run([sys.executable, str(probe)], capture_output=True,
                           text=True, env=env, timeout=60)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)
        self.assertIn("MODULE: None", r.stdout,
                      "the probe did not reach the broken checkout")
        why = [l for l in r.stdout.splitlines() if l.startswith("WHY:")][0]
        self.assertIn("RuntimeError", why,
                      "the import error reached nobody; `why` said nothing and "
                      "the caller printed its own fallback sentence instead")
        self.assertIn("half a checkout", why)

    def test_a_kernel_that_imports_leaves_why_empty(self):
        """`why` still answers `""` for a framework that is fine, or every hook
        starts printing a reason for a stand-down that did not happen."""
        sys.path.insert(0, str(ROOT / "hooks"))
        self.addCleanup(lambda: sys.path.remove(str(ROOT / "hooks")))
        import _framework  # noqa: PLC0415
        self.assertIsNotNone(_framework.ledger(ROOT))
        self.assertEqual("", _framework.why(ROOT))


class TheRegistryCheckerSaysWhenTheScanDidNotRun(unittest.TestCase):
    """`checkers/registry_consistency.py::_absence_still_holds`.

    This is the only reader `facts.absent` has. Both an unreadable table and a
    raising `facts.propose` returned `[]`, which `check` treats as "no
    problems": exit 0, "21 kind(s) … all agree", nothing on screen, with the
    re-check never having happened.
    """

    def setUp(self):
        self.rc = _load(self, "rc_probe",
                        ROOT / "checkers" / "registry_consistency.py")

    def test_a_facts_table_that_will_not_read_is_a_problem(self):
        root = _bare_repo(self)
        (root / ".v4" / "facts.probe.json").write_text("{not json",
                                                       encoding="utf-8")
        problems = self.rc._absence_still_holds(root)
        self.assertTrue(problems, "an unreadable facts table read as PASS")
        self.assertIn("facts.probe.json", problems[0])
        self.assertIn("does not read", problems[0])

    def test_a_scan_that_raises_is_a_problem(self):
        root = _bare_repo(self)
        (root / ".v4" / "facts.probe.json").write_text(json.dumps({
            "absent": {"outbound_write": "this library writes nothing, "
                                         "checked by hand on 2026-08-27"},
        }), encoding="utf-8")
        from kernel import facts as facts_mod
        with mock.patch.object(facts_mod, "propose",
                               side_effect=RuntimeError("no tree to walk")):
            problems = self.rc._absence_still_holds(root)
        self.assertTrue(problems, "a scan that raised read as PASS")
        self.assertIn("did not run", problems[0])
        self.assertIn("no tree to walk", problems[0])

    def test_a_declaration_that_still_holds_is_still_quiet(self):
        """The verdict this must not start inventing: a table that reads, a
        scan that runs, and nothing it finds."""
        root = _bare_repo(self)
        (root / ".v4" / "facts.probe.json").write_text(json.dumps({
            "absent": {"outbound_write": "this library writes nothing, "
                                         "checked by hand on 2026-08-27"},
        }), encoding="utf-8")
        from kernel import facts as facts_mod
        with mock.patch.object(facts_mod, "propose", return_value={}):
            self.assertEqual([], self.rc._absence_still_holds(root))


class TheFailClosedDetectorSaysWhenItSkippedAGoFile(unittest.TestCase):
    """`detectors/fail_closed.py::scan`.

    The Go branch skipped a file whose shape is `None` with a vestigial `pass`
    and no `warn()`, while every other skip in the same function warns -- so a
    missing Go toolchain, or one unparseable `.go` file, produced no claim and
    not one word on stderr. The checker's matching branch records that state as
    `unverifiable` rather than clean.

    The `.go` file here really will not parse, so `gosource.shape` answers
    `None` through its own code path with or without a Go toolchain on the
    machine.
    """

    def test_an_unparseable_go_file_is_named(self):
        det = _load(self, "fc_probe", ROOT / "detectors" / "fail_closed.py")
        root = _bare_repo(self)
        (root / "broken.go").write_text("package main\nfunc ( {\n",
                                        encoding="utf-8")
        said = []
        claims = det.scan(root, ["broken.go"], said.append)
        self.assertEqual([], claims)
        self.assertEqual(1, len(said),
                         "a Go file the detector could not read produced no "
                         "claim and no warning, which is what a Go file "
                         "answering every error also produces")
        self.assertIn("broken.go", said[0])
        self.assertIn("no Go toolchain, or it will not parse", said[0])

    def test_a_python_file_it_can_read_still_warns_about_nothing(self):
        """The other skips were already right, and stay right: a file that
        reads and parses is scanned, not warned about."""
        det = _load(self, "fc_probe2", ROOT / "detectors" / "fail_closed.py")
        root = _bare_repo(self)
        (root / "fine.py").write_text("def f():\n    return 1\n",
                                      encoding="utf-8")
        said = []
        det.scan(root, ["fine.py"], said.append)
        self.assertEqual([], said)


class TheDoctrineCheckReportsDriftInsteadOfRaising(unittest.TestCase):
    """`kernel/doctrine.py::drift`.

    `split` returns a non-`None` block for a file carrying `BEGIN` with no
    `END` -- deliberately, so `write` replaces the orphan rather than appending
    a second block under it -- and `drift` then re-derived the same split with
    `have_text.index(END)`, which raises. `registry_consistency` wraps the call
    in `except Exception` and turns it into "could not check the doctrine file:
    substring not found", about a file whose end marker somebody deleted.
    """

    def _repo_with_a_written_block(self):
        root = _bare_repo(self, doctrine=True)
        cfg = config.RepoConfig(root)
        path, _ = doctrine.write(cfg)
        return cfg, path

    def test_a_block_with_no_end_marker_is_a_sentence_not_a_valueerror(self):
        cfg, path = self._repo_with_a_written_block()
        self.assertIsNone(doctrine.drift(cfg), "the written block already drifts")
        path.write_text(path.read_text(encoding="utf-8").replace(doctrine.END, ""),
                        encoding="utf-8")
        problem = doctrine.drift(cfg)          # ValueError before this repair
        self.assertIsInstance(problem, str)
        self.assertIn("never closes it", problem)
        self.assertIn("will replace all of it", problem)

    def test_the_checker_that_calls_it_reports_the_same_thing(self):
        """Through `registry_consistency`, because that is where the
        `ValueError` surfaced -- as a sentence about a substring."""
        rc = _load(self, "rc_probe2",
                   ROOT / "checkers" / "registry_consistency.py")
        cfg, path = self._repo_with_a_written_block()
        path.write_text(path.read_text(encoding="utf-8").replace(doctrine.END, ""),
                        encoding="utf-8")
        _kinds, problems = rc.check(cfg.root)
        self.assertTrue(any("never closes it" in p for p in problems), problems)
        self.assertFalse(any("substring not found" in p for p in problems),
                         problems)

    def test_a_file_with_no_markers_at_all_still_says_so(self):
        """The branch above it, unchanged: no block is not a truncated one."""
        cfg, path = self._repo_with_a_written_block()
        path.write_text("this repo wrote its own CLAUDE.md\n", encoding="utf-8")
        self.assertIn("carries no generated block", doctrine.drift(cfg))


if __name__ == "__main__":
    unittest.main()
