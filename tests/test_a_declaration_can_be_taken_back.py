"""Two corrections the framework had no way to make.

    python3 -m unittest tests.test_a_declaration_can_be_taken_back -v

A scope is declared before the work and `derive` raises claims over all of it,
so declaring `src/**` for work in `src/workers/**` pulls in every other file's
whole-file claims. The only correction on offer was to abandon and reopen --
which is measured elsewhere to make a delta gate stop asking about what it had
already found.

And a subject file this change deleted read as "file is missing", the same
sentence a typo gets, in three checkers.

Both fail against 0ad6b61.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import cli, config as config_mod, ledger, scope as scope_mod  # noqa: E402
from kernel.analysis import subject_files  # noqa: E402


def _repo(case, files):
    tmp = Path(tempfile.mkdtemp())
    case.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp, capture_output=True)
    (tmp / ".v4").mkdir()
    (tmp / ".v4" / "config.json").write_text(json.dumps(
        {"test_command": "true", "policy": "allow_accepted_risk"}))
    (tmp / ".v4" / "claim_kinds.json").write_text("{}")
    for rel, body in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "in"], cwd=tmp, capture_output=True)
    return tmp


WHY = ("this task was only ever going to touch the worker, and the wider glob "
       "was a declaration made before anybody looked")


class TakingBackWhatWasNeverTouched(unittest.TestCase):
    def _task(self, root, scope):
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=scope,
                      base_commit=base, created_at="2026-08-20T00:00:00+00:00")
        conn.commit()
        return conn, config_mod.RepoConfig(root)

    def test_a_glob_nothing_touched_can_be_dropped(self):
        root = _repo(self, {"src/worker.py": "x = 1\n", "docs/notes.md": "hi\n"})
        conn, cfg = self._task(root, ["src/**", "docs/**"])
        left = scope_mod.narrow(conn, cfg, task_id="t1", drop=["docs/**"],
                                why=WHY)
        self.assertEqual(left, ["src/**"])
        self.assertEqual(scope_mod.current_scope(conn, "t1"), ["src/**"])

    def test_a_glob_holding_work_cannot(self):
        root = _repo(self, {"src/worker.py": "x = 1\n", "docs/notes.md": "hi\n"})
        conn, cfg = self._task(root, ["src/**", "docs/**"])
        (root / "docs" / "notes.md").write_text("edited\n")
        with self.assertRaises(scope_mod.NarrowRefused) as caught:
            scope_mod.narrow(conn, cfg, task_id="t1", drop=["docs/**"], why=WHY)
        self.assertIn("docs/notes.md", str(caught.exception))
        self.assertEqual(scope_mod.current_scope(conn, "t1"),
                         ["src/**", "docs/**"])

    def test_an_untracked_file_counts_as_work(self):
        """`checkers/scope.py` asks git both questions; so does this."""
        root = _repo(self, {"src/worker.py": "x = 1\n"})
        conn, cfg = self._task(root, ["src/**", "docs/**"])
        (root / "docs").mkdir(exist_ok=True)
        (root / "docs" / "new.md").write_text("written this task\n")
        with self.assertRaises(scope_mod.NarrowRefused):
            scope_mod.narrow(conn, cfg, task_id="t1", drop=["docs/**"], why=WHY)

    def test_a_glob_that_is_not_in_scope_is_refused(self):
        root = _repo(self, {"src/worker.py": "x = 1\n"})
        conn, cfg = self._task(root, ["src/**"])
        with self.assertRaises(scope_mod.NarrowRefused) as caught:
            scope_mod.narrow(conn, cfg, task_id="t1", drop=["nowhere/**"],
                             why=WHY)
        self.assertIn("nothing to take back", str(caught.exception))

    def test_narrowing_needs_a_reason_like_widening(self):
        root = _repo(self, {"src/worker.py": "x = 1\n"})
        conn, cfg = self._task(root, ["src/**", "docs/**"])
        with self.assertRaises(scope_mod.NarrowRefused):
            scope_mod.narrow(conn, cfg, task_id="t1", drop=["docs/**"],
                             why="too wide")

    def test_a_widen_after_a_narrow_wins(self):
        """The last statement about a path is the one that counts, which is
        what both commands mean."""
        root = _repo(self, {"src/worker.py": "x = 1\n", "docs/notes.md": "hi\n"})
        conn, cfg = self._task(root, ["src/**"])
        scope_mod.narrow(conn, cfg, task_id="t1", drop=["src/**"], why=WHY)
        self.assertEqual(scope_mod.current_scope(conn, "t1"), [])
        scope_mod.widen(conn, cfg, task_id="t1", add=["src/**"],
                        why="src/** is where the worker lives after all, and "
                            "this task has to touch it to finish what it "
                            "opened for")
        self.assertIn("src/**", scope_mod.current_scope(conn, "t1"))



class ADroppedGlobIsNotTheSameAsALostPath(unittest.TestCase):
    """The gate asked which globs a path matches, not whether it stays in scope.

    A scope can name a path twice -- `src/**` and `src/workers/job.py` -- and
    dropping the wide one moves nothing: every claim over that file still
    reaches this task through the narrow one. `matches(p, drop)` was a proxy for
    "out of sight" and diverged on exactly the correction `narrow`'s docstring
    exists for, once the work has started.

    Measured 2026-08-26, twice in one day: a `tests/**` that could not be
    narrowed cost a `baseline_raise` on a file the task never touched, and a
    `kernel/**` that could not be narrowed cost an abandoned task -- the outcome
    the docstring calls "the only correction on offer" and was written to
    replace.
    """

    def _task(self, root, scope):
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=scope,
                      base_commit=base, created_at="2026-08-20T00:00:00+00:00")
        conn.commit()
        return conn, config_mod.RepoConfig(root)

    def test_the_wide_glob_goes_when_a_narrow_one_still_covers_the_work(self):
        root = _repo(self, {"src/workers/job.py": "x = 1\n",
                            "src/other/thing.py": "y = 2\n"})
        conn, cfg = self._task(root, ["src/**", "src/workers/job.py"])
        (root / "src" / "workers" / "job.py").write_text("edited\n")
        left = scope_mod.narrow(conn, cfg, task_id="t1", drop=["src/**"],
                                why=WHY)
        self.assertEqual(left, ["src/workers/job.py"])

    def test_the_work_is_still_held_when_nothing_else_covers_it(self):
        """The half that must not move: dropping the only glob over a changed
        file is still refused, which is the whole reason the gate is there."""
        root = _repo(self, {"src/workers/job.py": "x = 1\n"})
        conn, cfg = self._task(root, ["src/**"])
        (root / "src" / "workers" / "job.py").write_text("edited\n")
        with self.assertRaises(scope_mod.NarrowRefused) as caught:
            scope_mod.narrow(conn, cfg, task_id="t1", drop=["src/**"], why=WHY)
        self.assertIn("src/workers/job.py", str(caught.exception))
        self.assertEqual(scope_mod.current_scope(conn, "t1"), ["src/**"])



class TheFlagThatWentNowhere(unittest.TestCase):
    """`scope narrow --add` parsed, and the branch passed only `drop` on.

    `--add`, `--drop` and `--why` live on one subparser shared by widen, narrow
    and show, so `scope narrow --drop a/** --add a/b.py` was accepted, took the
    drop, dropped the add on the floor and said nothing. That is the obvious way
    to replace a wide glob with a narrow one, and it failed in silence -- which
    is how it was first attempted here, twice.
    """

    def _args(self, root, **kw):
        a = dict(repo=str(root), acceptance=".v4/acceptance.json",
                 action="narrow", task="t1", add=None, drop=["src/**"],
                 why=WHY)
        a.update(kw)
        return types.SimpleNamespace(**a)

    def _task(self, root, scope):
        conn = ledger.connect(root)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        ledger.insert(conn, "task", id="t1", request="r", scope_globs=scope,
                      base_commit=base, created_at="2026-08-20T00:00:00+00:00")
        conn.commit()
        conn.close()

    def _run(self, args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.cmd_scope(args)
        return code, out.getvalue(), err.getvalue()

    def test_an_add_on_narrow_is_refused_rather_than_ignored(self):
        root = _repo(self, {"src/worker.py": "x = 1\n"})
        self._task(root, ["src/**", "docs/**"])
        code, _out, err = self._run(
            self._args(root, add=["src/worker.py"], drop=["docs/**"]))
        self.assertEqual(code, 2)
        self.assertIn("`--add` is `widen`", err)
        conn = ledger.connect(root)
        try:
            self.assertEqual(scope_mod.current_scope(conn, "t1"),
                             ["src/**", "docs/**"],
                             "a refused command must not have dropped anything")
        finally:
            conn.close()

    def test_narrow_without_an_add_still_works(self):
        root = _repo(self, {"src/worker.py": "x = 1\n"})
        self._task(root, ["src/**", "docs/**"])
        code, _out, _err = self._run(self._args(root, drop=["docs/**"]))
        self.assertEqual(code, 0)


class TheHookReadsTheSameScope(unittest.TestCase):
    """`hooks/write_block.py` keeps its own fallback read of the scope.

    It has to -- a hook fires from the coding agent's settings, in a process
    this framework did not start, and must work when the kernel is out of
    reach. What it must not do is answer a *different* question: a fallback
    that knew only about widening would keep allowing writes to a glob the task
    had taken back.
    """

    def test_both_halves_agree_after_a_narrow(self):
        sys.path.insert(0, str(ROOT / "hooks"))
        import write_block
        root = _repo(self, {"src/a.py": "x = 1\n", "docs/n.md": "hi\n"})
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True).stdout.strip()
        ledger.insert(conn, "task", id="t1", request="r",
                      scope_globs=["src/**", "docs/**"], base_commit=base,
                      created_at="2026-08-20T00:00:00+00:00")
        conn.commit()
        scope_mod.narrow(conn, config_mod.RepoConfig(root), task_id="t1",
                         drop=["docs/**"], why=WHY)
        conn.commit()
        self.assertEqual(scope_mod.current_scope(conn, "t1"), ["src/**"])
        self.assertEqual(write_block.current_scope(root, "t1"), ["src/**"])


class WhatOutlivedItsTaskAndWhatIsNotCode(unittest.TestCase):
    """The row that counts findings nobody settled, and what it must not count.

    Measured when it was first written: 27, of which 14 were `fail-closed`
    firing on `tests/fixtures/*/red/` -- files broken on purpose, and named by
    `derive_exclude` ever since. A row that is half noise is one people stop
    reading, which is the failure it exists to prevent.
    """

    def _task_with_a_failing_claim(self, root, file):
        conn = ledger.connect(root)
        self.addCleanup(conn.close)
        ledger.insert(conn, "task", id="t-old", request="r", scope_globs=["**"],
                      base_commit="", created_at="2026-08-06T00:00:00+00:00")
        ledger.insert(conn, "claim", id="c-old", task_id="t-old",
                      kind="fail-closed", question="q", subject_refs=[],
                      checker="fail-closed", origin="derive", file=file,
                      symbol="go", variant=None, line=None, note=None,
                      detector=None, detector_sha=None,
                      created_at="2026-08-06T00:00:00+00:00")
        ledger.append_attempt(
            conn, claim_id="c-old", subject_digest={}, checker_sha="s",
            config_sha="c", head_commit="h", worktree=str(root), facts_sha="",
            argv=["x"], exit_code=1, stdout="", stderr="", duration_ms=1,
            started_at="2026-08-06T00:00:01+00:00",
            ended_at="2026-08-06T00:00:01+00:00")
        ledger.insert(conn, "event", task_id="t-old", claim_id=None,
                      kind="abandoned", actor="worker", payload={"why": "over"},
                      created_at="2026-08-06T00:00:02+00:00")
        conn.commit()
        return conn

    def test_a_finding_about_real_code_is_counted(self):
        root = _repo(self, {"src/a.py": "x = 1\n"})
        conn = self._task_with_a_failing_claim(root, "src/a.py")
        got = ledger.unsettled_fails(conn, "t-old", ["tests/fixtures/**"])
        self.assertEqual([r[0] for r in got], ["c-old"])

    def test_and_one_about_a_deliberately_broken_fixture_is_not(self):
        root = _repo(self, {"tests/fixtures/red/broken.py": "x = 1\n"})
        conn = self._task_with_a_failing_claim(
            root, "tests/fixtures/red/broken.py")
        self.assertEqual(
            ledger.unsettled_fails(conn, "t-old", ["tests/fixtures/**"]), [])
        self.assertEqual(
            [r[0] for r in ledger.unsettled_fails(conn, "t-old")], ["c-old"],
            "without the exclusion it is counted, which is what it used to do")


class WhichKindOfMissing(unittest.TestCase):
    """"file is missing" was the answer for a typo and for a deliberate delete."""

    def test_git_names_what_this_change_deleted(self):
        root = _repo(self, {"app.py": "x = 1\n", "gone.py": "y = 2\n"})
        (root / "gone.py").unlink()
        self.assertEqual(subject_files.deleted_since(root), frozenset({"gone.py"}))

    def test_and_a_path_that_never_existed_is_not_in_it(self):
        root = _repo(self, {"app.py": "x = 1\n"})
        self.assertNotIn("typo.py", subject_files.deleted_since(root))


def _checker(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"_probe_{name}", ROOT / "checkers" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class ADeletionDoesNotHoldTheVerdict(unittest.TestCase):
    """Telling the two kinds of missing apart in prose was not enough.

    All three checkers put a deleted subject file and a mistyped one in the
    same `unverifiable` list, differing only in the sentence printed. One
    deleted file therefore returned 4 for the whole claim -- and
    `derive._retract_orphans` only retracts when *every* subject file is gone,
    so a claim over 22 deleted files and 4 live ones could be neither answered
    nor retracted. The checker's own text promised a retraction the kernel
    would not make.

    A file that is not in the tree holds no credential, no fail-open handler
    and no outbound write. It is listed, and then it stands aside.
    """

    def _split(self, name):
        """(deleted, unverifiable) for a subject of one deletion and one typo."""
        root = _repo(self, {"app.py": "x = 1\n", "gone.py": "y = 2\n"})
        (root / "gone.py").unlink()
        mod = _checker(name)
        rels = ["gone.py", "typo.py"]
        if name == "secret_scan":
            *_head, unverifiable, deleted = mod.inspect(root, rels)
        elif name == "external_write":
            from kernel.analysis import external_write as ew_analysis
            _f, unverifiable, deleted = mod.inspect(root, rels,
                                                    ew_analysis.default_table())
        else:
            _f, unverifiable, deleted = mod.inspect(root, rels)
        return dict(deleted), dict(unverifiable)

    def _assert_split(self, name):
        deleted, unverifiable = self._split(name)
        self.assertIn("gone.py", deleted, "the deletion is listed on its own")
        self.assertNotIn("gone.py", unverifiable, "and no longer holds the verdict")
        self.assertEqual(unverifiable.get("typo.py"), "file is missing",
                         "a path git never knew still cannot be verified")

    def test_secret_scan_sets_the_deletion_aside(self):
        self._assert_split("secret_scan")

    def test_fail_closed_sets_the_deletion_aside(self):
        self._assert_split("fail_closed")

    def test_external_write_sets_the_deletion_aside(self):
        self._assert_split("external_write")

    def test_a_subject_of_only_deletions_is_answered_not_shrugged_at(self):
        """The stuck claim's exact shape: nothing left to read, so nothing to fail on.

        Before this, `scanned` was empty and the `not scanned` branch returned
        4, so the claim could never reach a terminal state on its own.
        """
        root = _repo(self, {"app.py": "x = 1\n", "gone.py": "y = 2\n"})
        (root / "gone.py").unlink()
        from kernel.analysis import external_write as ew_analysis
        quiet = lambda *_a: None  # noqa: E731

        fc = _checker("fail_closed")
        f, unver, dele = fc.inspect(root, ["gone.py"])
        self.assertEqual(
            fc.report(f, unver, [], "", "", out=quiet, deleted=dele),
            0, "a deletion introduces no fail-open handler")

        ew = _checker("external_write")
        f, unver, dele = ew.inspect(root, ["gone.py"], ew_analysis.default_table())
        self.assertEqual(
            ew.report(f, unver, [], "", "", "<t>", out=quiet, deleted=dele),
            0, "a deletion issues no outbound write")

        ss = _checker("secret_scan")
        find, sup, scanned, oos, unver, dele = ss.inspect(root, ["gone.py"])
        self.assertEqual(
            ss.report(find, sup, scanned, oos, unver, out=quiet, deleted=dele),
            0, "a deletion carries no committed credential")


if __name__ == "__main__":
    unittest.main(verbosity=2)
