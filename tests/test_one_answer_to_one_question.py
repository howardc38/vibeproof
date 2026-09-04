"""Repairs in `kernel/config.py`, `kernel/facts.py`, `kernel/coverage.py`,
`kernel/doctrine.py`, `kernel/composition.py`, `kernel/analysis/` and the
monitor brief.

    python3 -m unittest tests.test_one_answer_to_one_question -v

Each of these is a place where one question had two answers, or where a number
in a comment had stopped describing the list beneath it.

All of them fail against 0ad6b61.
"""

from __future__ import annotations

import ast
import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, composition, config, coverage, doctrine, ledger  # noqa: E402
from kernel.analysis import (subject_files, test_expectation, test_shape,  # noqa: E402
                             test_weakened)


def registry_kinds():
    """Every kind this repo registers, as `coverage.report` wants them."""
    return json.loads((ROOT / ".v4" / "claim_kinds.json").read_text()).keys()


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


class TheFifthNumberThatGatesSomething(unittest.TestCase):
    """Four thresholds were type-checked at load and `lens_sweep.every_days`
    was not.

    `sweep.due` turns it into a `timedelta` and `sweep_current` calls `float`
    on it, so a string or a null there raised TypeError from inside the gate --
    the exact failure the thresholds paragraph in the same function describes.
    """

    def test_a_string_interval_is_a_config_error(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.RepoConfig(_repo(self, lens_sweep={"every_days": "4"}))
        self.assertIn("every_days", str(caught.exception))

    def test_a_negative_one_too(self):
        with self.assertRaises(config.ConfigError):
            config.RepoConfig(_repo(self, lens_sweep={"every_days": -1}))

    def test_and_a_number_is_fine(self):
        cfg = config.RepoConfig(_repo(self, lens_sweep={"every_days": 4}))
        self.assertEqual(cfg.config["lens_sweep"]["every_days"], 4)

    def test_and_saying_nothing_is_fine(self):
        self.assertTrue(config.RepoConfig(_repo(self)))

    def test_what_a_repo_declared_is_none_when_it_said_nothing(self):
        """`declared` is the reader for exactly these optional blocks: absent,
        empty and still-`UNANSWERED` are one state, and that state is exit 4
        rather than an answer in either direction."""
        cfg = config.RepoConfig(_repo(self, lens_sweep={"every_days": 4}))
        self.assertIsNone(config.declared(cfg.config, "runtime_proof"))
        self.assertEqual(config.declared(cfg.config, "lens_sweep"),
                         {"every_days": 4})


class TheCommandsThatMaintainTheFactsTable(unittest.TestCase):
    """`propose`, `validate`, `verify` and `scan` were reachable only as
    `python3 -m kernel.facts`.

    `docs/README.md` makes `verify` the verification story for a file 30% of
    this repo's claims depend on and CI calls it, while `USING.md` tells a
    newcomer that every command goes through `./bin/v4` -- so the four were
    absent from `v4 --help` entirely.
    """

    def _say(self, argv):
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            code = cli.main(argv)
        return code, said.getvalue()

    def test_validate_reads_this_repos_own_table(self):
        code, said = self._say(["--repo", str(ROOT), "facts", "validate"])
        self.assertEqual(code, 0, said)
        self.assertIn("auth_decision", said)

    def test_verify_takes_the_flag_ci_passes(self):
        code, said = self._say(["--repo", str(ROOT), "facts", "verify",
                                "--gone-only"])
        self.assertIn(code, (0, 1), said)
        self.assertIn("gone", said)


class WhatTheMonitorIsToldToRun(unittest.TestCase):
    """The brief named `facts-current`, and there is no such claim kind.

    31 kinds are registered and the nearest are `facts-coverage` (which asks
    whether this change added an undeclared call) and `sweep-current`. So the
    first of the four things that brief exists to audit had no trigger at all,
    while the only implementation of "the table is older than the code" --
    `facts.tree_drift` -- was reachable from nothing the brief named.
    """

    def test_the_drift_check_the_brief_now_names_answers(self):
        from kernel import facts as facts_mod
        table = facts_mod.load(str(config.facts_path_for(ROOT)))
        got = facts_mod.tree_drift(table, ROOT)
        self.assertIsInstance(got, list)

    def test_and_it_is_reachable_through_the_launcher(self):
        said = io.StringIO()
        with contextlib.redirect_stdout(said), contextlib.redirect_stderr(said):
            code = cli.main(["--repo", str(ROOT), "facts", "verify", "--gone-only"])
        self.assertIn(code, (0, 1), said.getvalue())
        self.assertNotIn("facts-current", said.getvalue())


class WhatPairsCannotHoldAtOnce(unittest.TestCase):
    """"With no task work between them" -- the clause the code never tested.

    Every ordinary task produced pairs: run check, edit a file, run check
    again. Measured before the repair, `v4 audit --compositions` reported
    t-003, t-004 and t-005 each as "1 claim pair(s) cannot hold at once" and
    exited 1.
    """

    def _task(self, conn, kinds):
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-19T00:00:00+00:00")
        for cid, kind in kinds:
            ledger.insert(conn, "claim", id=cid, task_id="t1", kind=kind,
                          question="q", subject_refs=[], checker=kind,
                          origin="derive", file=None, symbol=None, variant=None,
                          line=None, note=None, detector=None, detector_sha=None,
                          created_at="2026-08-19T00:00:00+00:00")

    def _attempt(self, conn, cid, stamp, at):
        ledger.append_attempt(
            conn, claim_id=cid, subject_digest="d", checker_sha="s",
            config_sha="c", head_commit="h", worktree=stamp, facts_sha="",
            argv=["x"], exit_code=0, stdout="", stderr="", duration_ms=1,
            started_at=at, ended_at=at)

    def test_a_worker_editing_between_two_checks_is_not_a_collision(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        kinds_cfg = {"scope": {"staleness": "repo"}, "test": {"staleness": "repo"}}
        self._task(conn, [("c1", "scope"), ("c2", "test")])
        self._attempt(conn, "c1", "tree-a", "2026-08-19T00:01:00+00:00")
        with ledger.writing(conn):
            ledger.insert(conn, "event", task_id="t1", claim_id=None,
                          kind="hook_seen", actor="worker",
                          payload={"allowed": 1, "basis": "cleared"},
                          created_at="2026-08-19T00:02:00+00:00")
        self._attempt(conn, "c2", "tree-b", "2026-08-19T00:03:00+00:00")
        self.assertEqual(composition.collisions(conn, "t1", kinds_cfg), [],
                         "run check, edit, run check is the ordinary sequence")

    def test_and_a_checker_writing_into_the_tree_it_judges_still_is(self):
        root = _repo(self)
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        kinds_cfg = {"scope": {"staleness": "repo"}, "test": {"staleness": "repo"}}
        self._task(conn, [("c1", "scope"), ("c2", "test")])
        self._attempt(conn, "c1", "tree-a", "2026-08-19T00:01:00+00:00")
        self._attempt(conn, "c2", "tree-b", "2026-08-19T00:03:00+00:00")
        got = composition.collisions(conn, "t1", kinds_cfg)
        self.assertTrue(got, "nobody wrote between them and the tree moved")


class OneOwnerForIsThisATest(unittest.TestCase):
    """`is_test_file` answers by name and `subject_files.is_test` reads the
    source, and the two disagree about `checkers/test_shape.py`.

    That is not drift: they answer different questions, and the module now says
    which -- a detector whose whole subject is a test file being emptied cannot
    ask what is inside the file.
    """

    def test_the_name_only_one_says_which_question_it_answers(self):
        self.assertTrue(test_weakened.is_test_file("tests/test_thing.py"))
        self.assertTrue(test_weakened.is_test_file("pkg/thing_test.py"))

    def test_and_the_content_one_does_not_call_a_checker_a_test(self):
        """A program *about* tests, named like one: the owner reads what is
        inside and says no."""
        self.assertFalse(subject_files.is_test(
            "checkers/test_shape.py",
            "import ast\n\n\ndef source_assertions(tree):\n"
            "    return [n for n in ast.walk(tree)]\n"))


class AnExpectationWrittenTheWayThisRepoWritesIt(unittest.TestCase):
    """`ASSERT_CALLS` was described as "calls whose expected value is a literal
    argument" and left out eight that are.

    Loosening `assertGreaterEqual(n, 10)` to `assertGreaterEqual(n, 1)` is
    exactly "edit the expectation to match the result", and `expectations()`
    recorded nothing for it -- in the spelling this repo's own suite uses 27
    times.
    """

    def _expectations(self, src):
        return test_expectation.expectations(ast.parse(src))

    def test_a_comparison_assert_carries_its_literal(self):
        got = self._expectations(
            "class T:\n    def test_a(self):\n"
            "        self.assertGreaterEqual(n, 10)\n")
        self.assertTrue(got, got)

    def test_and_loosening_it_is_visible(self):
        before = self._expectations(
            "class T:\n    def test_a(self):\n"
            "        self.assertGreaterEqual(n, 10)\n")
        after = self._expectations(
            "class T:\n    def test_a(self):\n"
            "        self.assertGreaterEqual(n, 1)\n")
        self.assertNotEqual(before, after)


class AParameterThatDecidesNothing(unittest.TestCase):
    """`source_assertions(tree, src)` never read `src`, and neither did
    `unbounded_fanout` -- three call sites carried a value that was not part
    of the contract."""

    def test_the_second_argument_is_optional(self):
        tree = ast.parse("import pathlib\n\n\ndef test_a():\n"
                         "    assert 'x' in pathlib.Path('a.py').read_text()\n")
        self.assertEqual(test_shape.source_assertions(tree),
                         test_shape.source_assertions(tree, "ignored"))
        self.assertTrue(test_shape.source_assertions(tree))


class TheJudgementsThisFrameworkHoldsByHand(unittest.TestCase):
    """A hand-written mapping row said "no surface checker yet" after one
    existed, and a comment said 79 beside a list of 90."""

    def test_the_predecessor_mapping_names_the_checkers_that_exist(self):
        registry = json.loads((ROOT / ".v4" / "checkers.json").read_text())
        for ids in coverage.MAPPING.values():
            for cid in ids:
                self.assertIn(cid, registry, cid)
        self.assertTrue(coverage.MAPPING["PO-6"], "surface-proof exists")

    def test_and_the_report_does_not_call_an_answered_family_unanswered(self):
        lines = coverage.report(ROOT, set(registry_kinds()))
        self.assertTrue(lines)
        po6 = [l for l in lines if "PO-6" in l]
        self.assertTrue(po6, lines)
        self.assertFalse([l for l in po6 if "0/" in l and "surface" in l], po6)

    def test_the_doctrine_count_is_counted(self):
        rules = doctrine.DOCTRINE
        self.assertGreater(len(rules), 1)
        text = doctrine.render(config.RepoConfig(_repo(self)))
        self.assertTrue(text.strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)
